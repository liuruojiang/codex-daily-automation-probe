"""Scoped native stock-input maintenance in a dedicated persistent checkout.

No NAV, strategy authority, signal, SMTP or orders are produced here. Historical
calculations retain their original full universe. Each successful source stage
is verified and recorded separately; another issuer's failure cannot erase it.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout, redirect_stderr
import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo
import subprocess
import sys
import traceback
import uuid

from prepare_microcap_local import atomic, encode, sha, writer_lock

RUNTIME = '3344e403ff01e17ef45b745a704a240e0917720b'
LEGACY_P2 = '3fe2e15ee9e3f2ee7e25bbc328479f1fd9910d36'
SOURCE_RUNTIMES = frozenset({RUNTIME,LEGACY_P2,'f6970d665d5c7a39b37c866cf13dc3d4cb8fdcc5','be89e87a0da728b27be0308d7c4c9e57a212b815','a016ce27ea86fca1fa0b5199f802b198c0ed99de',
    '706af7ea9186bdb9b3948738deb5a5afda7a331e','63c7b16d77600c4aa9dadf58b86d31e034477e44'})
BOOTSTRAP_RUNTIMES = {*SOURCE_RUNTIMES, '140b9c192e65a2b36a6b02106496e89f1143a4a6',
                      '24a41867408ebee570349de46d3ae37bcff2a6d4',
                      'c8d15e418c7003021866cd2aa4984e3e9a7103b6',
                      'a4adeacca194d5add4cfea0456c27e9da5444e45'}
CACHE = '.microcap_index_cache/'


def bound(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(root):
        raise ValueError('Source path escaped the declared root')
    # Native recovery closures can exceed Win32's ordinary path length. Keep
    # the confinement check above, then use the same extended path convention
    # as the native verifier for actual file I/O.
    if os.name=='nt' and not str(path).startswith('\\\\?\\'):
        return Path('\\\\?\\'+str(path))
    return path


def hashes(root, paths):
    return {name: sha(bound(root, name).read_bytes()) for name in sorted(set(paths))}


def read_bound_refs(value, base):
    result = {}
    if isinstance(value, dict):
        if isinstance(value.get('path'), str) and isinstance(value.get('sha256'), str):
            path = bound(base, value['path'])
            if sha(path.read_bytes()) != value['sha256']:
                raise ValueError('Bound input object hash differs')
            result[value['path']] = value['sha256']
        else:
            for nested in value.values(): result.update(read_bound_refs(nested, base))
    elif isinstance(value, list):
        for nested in value: result.update(read_bound_refs(nested, base))
    return result


class Journal:
    def __init__(self, directory, root, context):
        self.directory, self.root, self.context = Path(directory), Path(root), context
        self.directory.mkdir(parents=True, exist_ok=True)
        self.reused = 0

    def run(self, key, action, verify):
        if not key.replace('-', '').replace('_', '').isalnum():
            raise ValueError('Invalid stage identity')
        path = self.directory / (key + '.json')
        previous = json.loads(path.read_bytes()) if path.exists() else {}
        attempt = self.directory / ('attempt-' + key + '-' + uuid.uuid4().hex)
        attempt.mkdir()
        state = dict(status='running', context=self.context, key=key, pid=os.getpid(),
                     started_at=datetime.now(timezone.utc).isoformat(), attempt=str(attempt))
        atomic(path, encode(state))
        with (attempt / 'stdout.log').open('w', encoding='utf8') as stdout, \
             (attempt / 'stderr.log').open('w', encoding='utf8') as stderr:
            try:
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    # A changed controller/roster does not invalidate authentic
                    # native sources. Revalidate first, without querying them.
                    try:
                        files = verify()
                    except Exception:
                        # Previously complete bytes being changed/removed are
                        # corruption or concurrency, not a refresh permission.
                        if previous.get('status') == 'complete' and previous.get('context') == self.context:
                            raise
                        action()
                        files = verify()
                    else:
                        self.reused += 1
                    if not files or hashes(self.root, files) != files:
                        raise ValueError('Stage readback changed before receipt')
                state.update(status='complete', files=files)
            except Exception as exc:
                traceback.print_exc(file=stderr)
                state.update(status='failed', error_type=type(exc).__name__, error=str(exc))
        state['finished_at'] = datetime.now(timezone.utc).isoformat()
        atomic(attempt / 'result.json', encode(state))
        atomic(path, encode(state))
        return state


def validate_runtime(root, protected_root):
    root, protected_root = Path(root).resolve(), Path(protected_root).resolve()
    if root == protected_root or root.is_relative_to(protected_root) or protected_root.is_relative_to(root):
        raise ValueError('Scoped maintenance requires a separate checkout')
    actual = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    if actual not in (RUNTIME,LEGACY_P2):
        raise ValueError('Native maintenance runtime differs from the reviewed exact commit')
    profile='microcap_local_producer_runtime.json' if actual==RUNTIME else 'microcap_local_producer_p2_20261112.json'
    config=json.loads((Path(__file__).resolve().parents[1]/'config'/profile).read_bytes())
    if (config.get('schema')!='microcap_local_source_runtime_v1' or config.get('runtime_sha')!=actual
            or config.get('role')!='source_preparation_only' or config.get('max_candidates')!=300):
        raise ValueError('Local source producer role or runtime config differs')
    for name,digest in config['calculation_sources'].items():
        if sha(bound(root,name).read_bytes().replace(b'\r\n',b'\n'))!=digest:
            raise ValueError('Local source producer calculation source differs')
    dirty = subprocess.check_output(['git', '-C', str(root), 'diff', '--name-only', 'HEAD'], text=True).splitlines()
    if any(not name.startswith((CACHE, 'outputs/')) for name in dirty):
        raise ValueError('Native runtime has executable edits')
    return actual


def load_pool(path, target, *, run_directory=None):
    path = Path(path)
    summary = json.loads(path.read_bytes())
    if summary.get('schema') != 'microcap_local_screen_preparation_v1' or summary.get('status') != 'PREPARATION_VERIFIED':
        raise ValueError('Scoped maintenance requires a verified preparation roster')
    if not 0 <= (date.fromisoformat(target) - date.fromisoformat(summary['date'])).days <= 7:
        raise ValueError('Weekly preparation roster is stale or from the future')
    pool_path = Path(run_directory if run_directory is not None else summary['run_directory']) / 'candidates.json'
    raw = pool_path.read_bytes()
    if sha(raw) != summary['candidates_sha256']:
        raise ValueError('Candidate roster binding differs')
    pool = json.loads(raw)
    symbols = [r['symbol'] for r in pool['candidates']]
    if len(symbols) != 300 or len(set(symbols)) != 300 or sum(r['current_member'] for r in pool['candidates']) != 100:
        raise ValueError('Scoped roster must contain 300 unique issuers and 100 current members')
    return summary, sorted(symbols), sha(raw)


def bootstrap_coverage(record, symbols):
    """Report incomplete initialization without abandoning independent issuers."""
    expected = set(symbols)
    declared = record.get('symbols', [])
    initialized = record.get('initialized_symbols', [])
    if (len(symbols) != 300 or len(expected) != 300 or len(declared) != 300
            or set(declared) != expected or not isinstance(initialized, list)
            or len(initialized) != len(set(initialized))
            or record.get('status') not in ('complete', 'partial')):
        raise ValueError('Bootstrap does not bind the exact current 300-issuer roster')
    missing = sorted(expected - set(initialized))
    return dict(status='complete' if not missing and record['status'] == 'complete'
                and not record.get('failures') else 'incomplete', missing_symbols=missing)


def require_complete_bootstrap(record, symbols):
    coverage = bootstrap_coverage(record, symbols)
    if coverage['status'] != 'complete':
        raise ValueError('Incomplete bootstrap cannot certify all 300 metadata issuers: '
                         + ','.join(coverage['missing_symbols']))
    return coverage


def bootstrap(root, source, symbols, state, *, runtime_sha=None):
    """Copy exact verified source objects once, without changing the original."""
    from scripts.share_change_source import verify_share_cache
    from scripts.st_history_scope import source_closure
    state = Path(state)
    receipt = state / 'bootstrap.json'
    previous = None
    if receipt.exists():
        previous = json.loads(receipt.read_bytes())
        if (previous.get('status') not in ('complete', 'partial') or previous.get('runtime_sha') not in BOOTSTRAP_RUNTIMES
                or Path(previous['source_root']).resolve() != Path(source).resolve()):
            raise ValueError('Existing bootstrap has another runtime or source')
        # Mutable current objects may already have advanced. Do not reset them.
        if previous['status'] == 'complete' and previous['symbols'] == symbols: return previous
        atomic(state / 'bootstrap-revisions' / (sha(receipt.read_bytes()) + '.json'), receipt.read_bytes())
    copied = dict(previous.get('files', {})) if previous else {}
    def copy(relative, expected=None):
        src, dst = bound(source, relative), bound(root, relative)
        raw = src.read_bytes()
        digest = sha(raw)
        if expected is not None and digest != expected:
            raise ValueError('Source changed during bootstrap: ' + relative)
        if dst.exists() and dst.read_bytes() != raw:
            tracked_static = {CACHE + n for n in ('active_universe.csv', 'current_st.csv', 'security_master.csv')}
            tracked = subprocess.run(['git', '-C', str(root), 'ls-files', '--error-unmatch', relative],
                                     capture_output=True).returncode == 0
            pristine = subprocess.run(['git', '-C', str(root), 'diff', '--quiet', 'HEAD', '--', relative]).returncode == 0
            if relative not in tracked_static or not tracked or not pristine:
                raise ValueError('Bootstrap refuses to overwrite existing different input: ' + relative)
            atomic(state / 'bootstrap-backup' / relative, dst.read_bytes())
            atomic(dst, raw)
        elif not dst.exists(): atomic(dst, raw)
        if src.read_bytes() != raw or dst.read_bytes() != raw:
            raise ValueError('Concurrent source mutation during bootstrap')
        copied[relative] = digest
    with writer_lock(state / 'writer.lock'):
        if previous is None:
            for relative in ('security_master.csv', 'active_universe.csv', 'current_st.csv'):
                copy(CACHE + relative)
            reference_raw = bound(source, 'outputs/wind_microcap_top_100_biweekly_thursday_16y_cached.csv').read_bytes()
            reference_rows = list(csv.DictReader(io.StringIO(reference_raw.decode('utf-8-sig'))))
            consumed_through = max(row['date'][:10] for row in reference_rows)
            date.fromisoformat(consumed_through)
            atomic(state / 'reference-proxy.csv', reference_raw)
            notice = source_closure(source)
            for relative, digest in notice.items(): copy(relative, digest)
            # Keep the provider stop, including its original evidence bytes.
            stop = CACHE + 'share_sources/RATE_LIMIT_STOP.json'
            if bound(source, stop).exists(): copy(stop)
        else:
            reference_raw = (state / 'reference-proxy.csv').read_bytes()
            if sha(reference_raw) != previous['reference_proxy_sha256']:
                raise ValueError('Original consumed-history reference changed')
            consumed_through = previous['reference_consumed_through']
        initialized = list(previous.get('initialized_symbols', [])) if previous else []
        failures = {}
        for number, symbol in enumerate(symbols, 1):
            if symbol in initialized: continue
            try:
                for group, suffix in (('prices_raw', '.csv'), ('share_change', '.csv'),
                                      ('security_meta', '.json'), ('trade_status', '.json')):
                    copy(CACHE + group + '/' + symbol + suffix)
                share_state = json.loads(bound(source, CACHE + 'share_sources/' + symbol + '.json').read_bytes())
                proof = verify_share_cache(root=source, symbol=symbol, as_of=share_state['query_end'])
                for relative, digest in proof['source_files'].items(): copy(CACHE + 'share_sources/' + relative, digest)
                status = json.loads(bound(source, CACHE + 'trade_status/' + symbol + '.json').read_bytes())
                for relative, digest in read_bound_refs(status, bound(source, CACHE + 'trade_status')).items():
                    copy(CACHE + 'trade_status/' + relative, digest)
                manifest_name = CACHE + 'return_basis/manifests/' + symbol + '.json'
                copy(manifest_name)
                manifest = json.loads(bound(source, manifest_name).read_bytes())
                copy(CACHE + 'return_basis/prices_adj/' + symbol + '.csv', manifest['candidate_sha256'])
                identity = sha(json.dumps(manifest, sort_keys=True).encode())
                bundle = CACHE + 'return_basis/bundles/' + symbol + '/' + identity + '/'
                copy(bundle + 'raw.csv', manifest['raw_sha256'])
                for name in ('qfq.js', 'hfq.js', 'qfq.json', 'hfq.json'):
                    copy(CACHE + 'return_basis/sources/' + symbol + '/' + name)
                initialized.append(symbol)
            except Exception as exc:
                failures[symbol] = dict(error_type=type(exc).__name__, error=str(exc))
            if number % 25 == 0: print(f'[bootstrap] verified exact copies {number}/{len(symbols)}', flush=True)
        result = dict(status='partial' if failures else 'complete', runtime_sha=runtime_sha or RUNTIME, source_root=str(source),
                      initialized_symbols=sorted(set(initialized)), failures=failures,
                      symbols=symbols, files=copied, reference_consumed_through=consumed_through,
                      reference_proxy_sha256=sha(reference_raw),
                      input_semantics='per-source native evidence; not whole strategy seed')
        atomic(receipt, encode(result))
        return result


def price_seed_anchor(root, symbol, baseline):
    expected = baseline['files'][CACHE + 'prices_raw/' + symbol + '.csv']
    candidates = [name for name, digest in baseline['files'].items()
                  if name.startswith(CACHE + 'return_basis/bundles/' + symbol + '/')
                  and name.endswith('/raw.csv') and digest == expected]
    if len(candidates) != 1: raise ValueError('Ambiguous immutable price seed')
    raw = bound(root, candidates[0]).read_bytes()
    if sha(raw) != expected: raise ValueError('Immutable price seed changed')
    rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
    return max(row['date'][:10] for row in rows)


def price_proof(root, symbol, target, master, anchor):
    import pandas as pd
    from scripts.exchange_calendar import sessions_for_day
    from scripts.tradeability_evidence import enrich_price_with_trade_status
    price = CACHE + 'prices_raw/' + symbol + '.csv'
    status = CACHE + 'trade_status/' + symbol + '.json'
    raw = pd.read_csv(bound(root, price), parse_dates=['date'])
    sessions = pd.DatetimeIndex(sessions_for_day(date.fromisoformat(target)))
    sessions = sessions[(sessions >= raw.date.min()) & (sessions <= pd.Timestamp(target))]
    frame = enrich_price_with_trade_status(root=root, symbol=symbol, raw_path=bound(root, price),
                     price=raw, master=master, trading_dates=sessions, require_evidence=True)
    # Preserve the approved historical seed and require every *new* session.
    # Native continuation already verifies original raw/sidecar lineage, all
    # bound provider rows, and rejects historical close/status rewrites.
    required = {d.strftime('%Y-%m-%d') for d in sessions if d.strftime('%Y-%m-%d') >= anchor}
    if not required or not required <= set(frame.attrs.get('verified_trade_status_dates', [])):
        raise ValueError('Incomplete native trade-state session closure')
    record = json.loads(bound(root, status).read_bytes())
    refs = read_bound_refs(record, bound(root, CACHE + 'trade_status'))
    return hashes(root, [price, status, CACHE + 'security_master.csv',
                         *[CACHE + 'trade_status/' + p for p in refs]])


def share_proof(root, symbol, target):
    from scripts.share_change_source import verify_share_cache
    result = verify_share_cache(root=root, symbol=symbol, as_of=target)
    return hashes(root, [CACHE + 'share_change/' + symbol + '.csv',
                         *[CACHE + 'share_sources/' + p for p in result['source_files']]])


def import_notice_evidence(source, root, target):
    """Reuse native authenticated evidence with its original dates and hashes."""
    from scripts.st_notice_refresh import FRESHNESS, freshness_source_files
    from scripts.st_history_scope import source_closure, resolve_archive_reference, POINTER_PATH
    source, root = Path(source).resolve(), Path(root).resolve()
    proof_raw = bound(source, FRESHNESS).read_bytes()
    proof = json.loads(proof_raw)
    current = list(resolve_archive_reference(root))
    if proof['target'] != target or current not in (proof['previous_authority'], proof['authority']):
        raise ValueError('Reusable notice evidence has another target or prior authority')
    closure = source_closure(source)
    closure.update(freshness_source_files(source))
    closure[FRESHNESS] = sha(proof_raw)
    original_pointer = bound(root, POINTER_PATH).read_bytes()
    for relative, digest in closure.items():
        if relative in (POINTER_PATH, FRESHNESS): continue
        raw = bound(source, relative).read_bytes()
        if sha(raw) != digest: raise ValueError('Notice source changed during import')
        path = bound(root, relative)
        if path.exists() and path.read_bytes() != raw:
            raise ValueError('Conflicting immutable notice source')
        if not path.exists(): atomic(path, raw)
    if bound(root, POINTER_PATH).read_bytes() != original_pointer:
        raise ValueError('Concurrent notice authority change')
    atomic(bound(root, POINTER_PATH), bound(source, POINTER_PATH).read_bytes())
    atomic(bound(root, FRESHNESS), proof_raw)
    freshness_source_files(root)


def ratio_proof(root, symbol, target, master):
    from scripts.stock_return_pipeline import load_return_prices
    raw_path = bound(root, CACHE + 'prices_raw/' + symbol + '.csv')
    load_return_prices(root=root, symbol=symbol, raw_path=raw_path, master=master,
                       as_of=target, allow_network=False)
    manifest_name = CACHE + 'return_basis/manifests/' + symbol + '.json'
    manifest = json.loads(bound(root, manifest_name).read_bytes())
    if manifest['as_of'] != target:
        raise ValueError('Return basis does not cover the requested session')
    identity = sha(json.dumps(manifest, sort_keys=True).encode())
    prefix = CACHE + 'return_basis/bundles/' + symbol + '/' + identity + '/'
    files = [manifest_name, CACHE + 'return_basis/prices_adj/' + symbol + '.csv',
             CACHE + 'prices_raw/' + symbol + '.csv', CACHE + 'security_master.csv']
    files += [prefix + 'raw.csv']
    files += [CACHE + 'return_basis/sources/' + symbol + '/' + name for name in ('qfq.js', 'hfq.js', 'qfq.json', 'hfq.json')]
    return hashes(root, files)


def refresh_metadata(root, freq, symbol, target):
    from scripts.st_metadata_refresh import frozen_notice_sources, _history_equal
    old_path = bound(root, CACHE + 'security_meta/' + symbol + '.json')
    oldraw = old_path.read_bytes(); old = json.loads(oldraw)
    stage = bound(root, CACHE + 'local_metadata_attempts/' + uuid.uuid4().hex)
    stage.mkdir(parents=True)
    atomic(stage / 'before.json', oldraw)
    namespace = freq.build_security_meta.__globals__
    original_dir = freq.SECURITY_META_DIR
    names = ('SECURITY_META_DIR', 'SHARED_SECURITY_META_DIR', 'fetch_cninfo_st_notices', 'fetch_sz_name_change_history')
    original = {n: namespace[n] for n in names if n in namespace}
    def forbidden(*args, **kwargs): raise ValueError('Frozen scoped metadata forbids unreviewed fallback')
    try:
        freq.SECURITY_META_DIR = stage
        namespace.update(SECURITY_META_DIR=stage, SHARED_SECURITY_META_DIR=None,
                         fetch_cninfo_st_notices=forbidden, fetch_sz_name_change_history=forbidden)
        with frozen_notice_sources(root):
            new = freq.build_security_meta(symbol, as_of=target)
        if (not new or not _history_equal(old, new, old['notice_query_end_date'])
                or new.get('st_notice_evidence_sha256') != old.get('st_notice_evidence_sha256')):
            raise ValueError('Scoped metadata changed verified historical semantics')
        candidate = (stage / (symbol + '.json')).read_bytes()
        if json.loads(candidate) != new or old_path.read_bytes() != oldraw:
            raise ValueError('Scoped metadata persistence or concurrent input mismatch')
        atomic(old_path, candidate)
    finally:
        freq.SECURITY_META_DIR = original_dir
        for n in names:
            if n in original: namespace[n] = original[n]
            else: namespace.pop(n, None)


def scoped_metadata_marker(root, scope_sha):
    if len(scope_sha)!=64 or any(c not in '0123456789abcdef' for c in scope_sha):
        raise ValueError('Invalid metadata roster identity')
    relative=CACHE+'local_metadata_scopes/'+scope_sha+'/transaction.json'
    # The native writer expects its marker parent to exist. Its default marker
    # lives directly in the cache, while this scoped marker has a new parent.
    bound(root,relative).parent.mkdir(parents=True,exist_ok=True)
    return relative


def observation_floor(now=None):
    now = now or datetime.now(timezone.utc)
    return now.astimezone(ZoneInfo('Asia/Shanghai')).replace(hour=9, minute=0, second=0, microsecond=0)


def select_notice_generation(root,target,required_asof,out):
    """Admit complete original pages only; Native still owns boundary promotion."""
    root=Path(root).resolve();parent=root/CACHE/'st_notice_sources'
    def regular(path,kind):
        path=Path(path)
        if not path.resolve().is_relative_to(root):raise ValueError('Notice resume source escaped root')
        for part in (path,*path.parents):
            if part==root:break
            if part.is_symlink() or getattr(part,'is_junction',lambda:False)():
                raise ValueError('Notice resume source contains a link')
        if not (path.is_dir() if kind=='directory' else path.is_file()):
            raise ValueError('Notice resume source is not a regular '+kind)
        return path
    if not parent.exists():return None
    regular(parent,'directory')
    candidates=[]
    for folder in parent.iterdir():
        if not folder.name.startswith('refresh_'):continue
        regular(folder,'directory')
        if not any((folder/name).exists() or (folder/name).is_symlink()
                   for name in ('proof.json','archive.json','previous_pointer.json')):
            candidates.append(folder)
    if not candidates:return None
    from scripts.st_history_scope import resolve_archive_reference,POINTER_PATH
    from scripts.st_notice_incremental import verify_day,StNoticeEvidenceError
    if (required_asof.tzinfo is None or required_asof>datetime.now(timezone.utc)):
        raise ValueError('Notice resume requires an actual past aware morning floor')
    files={}
    def original(name,digest=None):
        if Path(name).is_absolute() or '..' in Path(name).parts:raise ValueError('Nonportable notice resume source')
        path=regular(root/name,'file');raw=path.read_bytes();actual=sha(raw)
        if digest is not None and actual!=digest:raise ValueError('Notice resume authority source hash differs')
        files[path.relative_to(root).as_posix()]=actual
        return json.loads(raw)
    original(POINTER_PATH)
    reference=resolve_archive_reference(root)
    archive=original(reference[0],reference[1])
    if archive.get('schema_version')!=3 or not archive.get('daily_increments'):
        raise ValueError('Notice resume requires an installed all-class daily boundary')
    scope=original(archive['scope']['path'],archive['scope']['sha256'])
    cursor=date.fromisoformat(scope['end_date'])+timedelta(days=1)
    for item in archive['daily_increments']:
        if item['date']!=cursor.isoformat():raise ValueError('Notice resume installed boundary is not contiguous')
        cursor+=timedelta(days=1)
    boundary=cursor-timedelta(days=1);end=date.fromisoformat(target)
    if end<boundary:raise ValueError('Notice resume target precedes installed boundary')
    last=archive['daily_increments'][-1]['manifest'];original(last['path'],last['sha256'])
    days=[(boundary+timedelta(days=n)).isoformat() for n in range((end-boundary).days+1)]
    eligible=[]
    for folder in sorted(candidates):
        if any(not (folder/day/'manifest.json').exists() for day in days):continue
        observed=[];objects={};summaries=[];stale=False
        for day in days:
            daily=regular(folder/day,'directory');mp=regular(daily/'manifest.json','file')
            manifest=json.loads(mp.read_bytes())
            for number,page in enumerate(manifest['pages'],1):
                for role,suffix in (('response','.json'),('receipt','.receipt.json')):
                    if page.get(role)!=f'page_{number:04}'+suffix:raise ValueError('Notice resume page path differs')
                    regular(daily/page[role],'file')
            try:verified=verify_day(daily,day,required_asof=required_asof)
            except StNoticeEvidenceError as exc:
                if str(exc)!='Daily notice snapshot is older than required observation; use a new collection directory':raise
                stale=True;break
            objects[mp.relative_to(root).as_posix()]=verified['manifest_sha256']
            for name,digest in verified['source_files'].items():objects[(daily/name).relative_to(root).as_posix()]=digest
            observed.append(datetime.fromisoformat(verified['observation_end']))
            summaries.append(dict(date=day,pages=verified['pages'],observation_end=verified['observation_end']))
        if not stale:eligible.append((max(observed),folder,objects,summaries))
    if not eligible:return None
    eligible.sort(key=lambda item:item[0]);chosen=eligible[-1]
    if len(eligible)>1 and eligible[-2][0]==chosen[0]:raise ValueError('Notice resume newest observation is ambiguous')
    observed,folder,objects,summaries=chosen;files.update(objects)
    for name,digest in files.items():
        if sha(regular(root/name,'file').read_bytes())!=digest:raise ValueError('Notice resume source changed during admission')
    receipt=dict(schema='microcap_notice_generation_admission_v1',status='original_complete_generation_selected_native_gate_required',
        runtime_sha=RUNTIME,target=target,required_asof=required_asof.isoformat(),authority=list(reference),
        boundary=boundary.isoformat(),generation=folder.relative_to(root).as_posix(),observation_end=observed.isoformat(),
        days=summaries,source_files=files,native_verifier_sha256=sha(regular(root/'scripts/st_notice_incremental.py','file').read_bytes().replace(b'\r\n',b'\n')),
        original_pages_and_clocks_rewritten=False,native_boundary_gate_required=True)
    raw=encode(receipt);path=Path(out)/('notice-generation-admission-'+sha(raw)+'.json')
    if path.exists():
        if path.read_bytes()!=raw:raise ValueError('Notice generation admission changed')
    else:atomic(path,raw)
    return folder


def refresh_notice_with_existing_pages(*,root,target,required_asof,out,refresh,resume_generation=None):
    generation=resume_generation
    if generation is None:generation=select_notice_generation(root,target,required_asof,out)
    options=dict(resume_generation=generation,required_asof=required_asof) if generation is not None else {}
    return refresh(root=root,target=target,**options)


def select_local_notice_forward(*,root,protected_root,state,symbols,target,out,native=None,writer_locked=False):
    """Select an original same-authority receipt for the actual AB214 owner.

    The local run already owns writer.lock; independent callers acquire it.
    No authority or target freshness is earned here. Cold M7/M8 admission keeps
    its separate existing guard. All new control logic stays in this hashed file.
    """
    from contextlib import ExitStack
    module_path=Path(__file__).with_name('microcap_notice_prefix.py').resolve()
    # Pin this reused dependency through maintain's existing orchestrator hash.
    if sha(module_path.read_bytes().replace(b'\r\n',b'\n'))!='f6746db692d5a1af0baca6431cd774e048564a794d42e1a11484719a39fc57f0':
        raise ValueError('Local notice selector dependency is not the reviewed exact source')
    import microcap_notice_prefix as prefix
    if Path(prefix.__file__).resolve()!=module_path:raise ValueError('Foreign local notice selector module')
    roles=[Path(p).absolute() for p in (root,protected_root,state,out)]
    if any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for role in roles for p in (role,*role.parents)):
        raise ValueError('Local notice role contains a link')
    root,protected,state,out=map(lambda p:p.resolve(),roles)
    ab214='ab214c20d92ca9bbda1dde7e447bb153e7f59028'
    owner=subprocess.check_output(['git','-C',str(protected),'rev-parse','HEAD'],encoding='utf-8').strip()
    if owner in (prefix.CONSUMER,prefix.ACTIVE_CONSUMER):
        return dict(status='not_applicable_to_local_notice_forward',verification_consumer_runtime=owner,
                    source_inputs_only=True,target_freshness_granted=False,current_signal_ready=False)
    if owner!=ab214:raise ValueError('Local notice protected owner must be actual AB214')
    if (root==state or not root.is_relative_to(state) or root.is_relative_to(protected)
            or protected.is_relative_to(state) or state.is_relative_to(protected)
            or out.is_relative_to(root) or root.is_relative_to(out)
            or out.is_relative_to(protected) or protected.is_relative_to(out)):
        raise ValueError('Local notice selector roles overlap or escape the state owner')
    prefix.runtime_role(protected,ab214)
    reader=validate_runtime(root,protected)
    if reader!=RUNTIME:raise ValueError('Local notice selection requires the active P3 reader')
    if date.fromisoformat(target).isoformat()!=target:raise ValueError('Noncanonical local notice target')
    native=native or prefix.real_native
    with ExitStack() as locks:
        if not writer_locked:locks.enter_context(writer_lock(state/'writer.lock'))
        for name in ('worker-slot-0.lock','worker-slot-1.lock','price-worker.lock'):
            locks.enter_context(writer_lock(state/name))
        locks.enter_context(prefix.native_refresh_lock(root))
        proxy='outputs/wind_microcap_top_100_biweekly_thursday_16y_cached.csv'
        formal_name='outputs/top100_delivery_manifest.json'
        refs={p:p.read_bytes() for p in (state/'bootstrap.json',state/'reference-proxy.csv',protected/proxy,protected/formal_name)}
        record=json.loads(refs[state/'bootstrap.json']);require_complete_bootstrap(record,symbols)
        if (record.get('runtime_sha') not in BOOTSTRAP_RUNTIMES
                or Path(record.get('source_root','')).resolve()!=protected):
            raise ValueError('Local notice bootstrap belongs to another source owner')
        reference=refs[state/'reference-proxy.csv'];rows=list(csv.DictReader(io.StringIO(reference.decode('utf-8-sig'))))
        if (not rows or reference!=refs[protected/proxy] or sha(reference)!=record.get('reference_proxy_sha256')):
            raise ValueError('Local notice reference is not the actual protected proxy')
        anchor=max(r['date'][:10] for r in rows);date.fromisoformat(anchor)
        formal=json.loads(refs[protected/formal_name]);inputs={**formal.get('inputs',{}),**formal.get('artifacts',{})}
        if (record.get('reference_consumed_through')!=anchor or anchor>target or formal.get('ok') is not True
                or formal.get('status')!='complete' or formal.get('scope')!='whole_workspace_delivery'
                or formal.get('expected_date')!=anchor
                or inputs.get(proxy) not in (sha(reference),sha(reference.replace(b'\r\n',b'\n')))):
            raise ValueError('Local notice formal bootstrap anchor differs')
        for name in (prefix.POINTER,prefix.FRESHNESS):
            path=prefix.bound(protected,name);refs[path]=path.read_bytes() if path.exists() else None
        protected_pointer=refs[protected/prefix.POINTER]
        pointer_binding=inputs.get(prefix.POINTER,record.get('files',{}).get(prefix.POINTER))
        if (protected_pointer is None or pointer_binding not in (sha(protected_pointer),sha(protected_pointer.replace(b'\r\n',b'\n')))
                or record.get('files',{}).get(prefix.POINTER) not in (sha(protected_pointer),sha(protected_pointer.replace(b'\r\n',b'\n')))):
            raise ValueError('Local notice protected pointer is not its original bootstrap object')
        before=prefix._pair(root)
        if before[prefix.POINTER] is None:raise ValueError('Local notice current authority is missing')
        pointer=json.loads(before[prefix.POINTER]);authority=[pointer.get('manifest_path'),pointer.get('manifest_sha256')]
        archive_raw=prefix.bound(root,authority[0]).read_bytes();archive=json.loads(archive_raw)
        if sha(archive_raw)!=authority[1]:raise ValueError('Local notice authority archive bytes differ')
        authority_day=archive['daily_increments'][-1]['date']
        plan=native(root,root,'forward',requested_target=target)
        digest=sha(before[prefix.FRESHNESS]) if before[prefix.FRESHNESS] is not None else None
        if (plan.get('mode')!='forward' or plan.get('native_runtime')!=reader or plan.get('authority')!=authority
                or plan.get('authority_day')!=authority_day or plan.get('requested_target')!=target
                or plan.get('active_sha256')!=digest or plan.get('state') not in ('noop','select')
                or plan.get('old_prefix_unchanged') is not True or plan.get('original_clocks_retained') is not True
                or prefix._pair(root)!=before or not prefix._same(refs)):
            raise ValueError('Local notice original Native chain plan or owner changed')
        if before[prefix.FRESHNESS] is None and target>authority_day:
            known=json.loads(protected_pointer)
            if [known.get('manifest_path'),known.get('manifest_sha256')]!=authority:
                raise ValueError('Local notice unhanded authority has no active original proof')
        if target<authority_day:raise ValueError('Local notice target precedes current source authority')
        out.mkdir(parents=True,exist_ok=True);intent_path=out/'selection-intent.json';backup=out/'original-active-self.json'
        roots=dict(root=str(root),protected=str(protected),state=str(state))
        receipt=dict(schema='microcap_local_notice_forward_selection_v1',status='local_notice_forward_checkpoint_not_needed',
            verification_runtime=reader,verification_consumer_runtime=owner,protected_formal_date=anchor,
            protected_formal_manifest_sha256=sha(refs[protected/formal_name]),requested_target=target,authority=authority,
            authority_day=authority_day,authority_pointer_unchanged=True,original_receipt_bytes_and_clocks_preserved=True,
            target_freshness_granted=False,current_signal_ready=False,source_inputs_only=True,
            final_strategy_delivery=False,email_sent=False,orders_placed=False)
        if plan['state']=='select':
            active=json.loads(before[prefix.FRESHNESS]) if before[prefix.FRESHNESS] is not None else {}
            spec=plan.get('selected_spec') or {};self_spec=plan.get('active_spec') or {}
            expected=str(Path(authority[0]).with_name('proof.json')).replace('\\','/')
            if (not target>authority_day or active.get('authority')!=authority or active.get('previous_authority')!=authority
                    or active.get('target')!=authority_day or spec.get('path')!=expected
                    or not prefix.file_role(spec.get('path','')) or not prefix.file_role(self_spec.get('path',''))
                    or self_spec.get('sha256')!=digest or prefix.bound(root,self_spec['path']).read_bytes()!=before[prefix.FRESHNESS]):
                raise ValueError('Local notice selection is not an original same-authority self review')
            wanted=prefix.bound(root,spec['path']).read_bytes();advance=json.loads(wanted)
            if (sha(wanted)!=spec.get('sha256') or wanted==before[prefix.FRESHNESS]
                    or advance.get('authority')!=authority or advance.get('previous_authority')==authority
                    or advance.get('target')!=authority_day):raise ValueError('Local notice advancing original differs')
            source_files=plan.get('source_files')
            if (not isinstance(source_files,dict) or source_files.get(spec['path'])!=spec['sha256']
                    or source_files.get(self_spec['path'])!=self_spec['sha256']):
                raise ValueError('Local notice both original Native proofs are required')
            for name,expected_sha in source_files.items():
                if not prefix.file_role(name) or prefix.sha_file(prefix.bound(root,name))!=expected_sha:
                    raise ValueError('Local notice original chain object changed')
            intent=dict(schema='microcap_local_notice_forward_intent_v1',status='intent',roots=roots,requested_target=target,
                pointer_sha256=sha(before[prefix.POINTER]),authority=authority,original_active_sha256=digest,
                original_active_spec=self_spec,selected_spec=spec,protected_formal_manifest_sha256=receipt['protected_formal_manifest_sha256'])
            if intent_path.exists():raise ValueError('Local notice prior selection outcome is unknown; no second write')
            if backup.exists() and backup.read_bytes()!=before[prefix.FRESHNESS]:raise ValueError('Local notice original self backup differs')
            if not backup.exists():atomic(backup,before[prefix.FRESHNESS])
            atomic(intent_path,encode(intent))
            if prefix._pair(root)!=before or not prefix._same(refs):raise ValueError('Local notice CAS owner changed')
            reconciled=False
            try:prefix._write_pair_member(prefix.bound(root,prefix.FRESHNESS),wanted)
            except BaseException:
                if prefix._pair(root)!={prefix.POINTER:before[prefix.POINTER],prefix.FRESHNESS:wanted} or not prefix._same(refs):
                    atomic(intent_path,encode(dict(intent,status='write_not_confirmed')));raise
                reconciled=True
            if (prefix._pair(root)!={prefix.POINTER:before[prefix.POINTER],prefix.FRESHNESS:wanted} or not prefix._same(refs)
                    or prefix.bound(root,self_spec['path']).read_bytes()!=before[prefix.FRESHNESS]):
                raise ValueError('Local notice selected original readback changed')
            atomic(intent_path,encode(dict(intent,status='complete',write_response_lost_reconciled=reconciled)))
            receipt.update(status='original_local_notice_forward_checkpoint_selected',active_freshness_changed=True,
                selected_spec=spec,original_active_spec=self_spec,write_response_lost_reconciled=reconciled)
        elif intent_path.exists():
            old=json.loads(intent_path.read_bytes());spec=plan.get('selected_spec') or {}
            if (old.get('schema')!='microcap_local_notice_forward_intent_v1' or old.get('roots')!=roots
                    or old.get('requested_target')!=target or old.get('pointer_sha256')!=sha(before[prefix.POINTER])
                    or old.get('selected_spec')!=spec or spec.get('sha256')!=digest
                    or not backup.is_file() or prefix.sha_file(backup)!=old.get('original_active_sha256')
                    or prefix.sha_file(prefix.bound(root,old['original_active_spec']['path']))!=old.get('original_active_sha256')):
                raise ValueError('Local notice prior intent cannot be reconciled from original bytes')
            if old.get('status')!='complete':atomic(intent_path,encode(dict(old,status='reconciled')))
            receipt.update(status='original_local_notice_forward_checkpoint_selected',active_freshness_changed=False,
                selected_spec=spec,selection_reconciled=True)
        elif target>authority_day and before[prefix.FRESHNESS] is not None and json.loads(before[prefix.FRESHNESS]).get('previous_authority')==authority:
            raise ValueError('Local notice self review cannot skip its upcoming Native forward')
        if prefix._pair(root)[prefix.POINTER]!=before[prefix.POINTER] or not prefix._same(refs):
            raise ValueError('Local notice selection changed authority or protected anchor')
        if validate_runtime(root,protected)!=reader:raise ValueError('Local notice reader runtime changed')
        prefix.runtime_role(protected,ab214)
        atomic(out/'receipt.json',encode(receipt))
        return receipt


def current_names_proof(root, target, required_asof=None, symbols=None):
    """Current name cross-check only; native notice history still proves PIT ST."""
    name=CACHE+'current_st.csv';receipt=CACHE+'local_current_names/'+target+'.json'
    record=json.loads(bound(root,receipt).read_bytes())
    observed=datetime.fromisoformat(record['observed_at'])
    earliest=datetime.fromisoformat(target+'T15:00:00+08:00')
    if required_asof is not None: earliest=max(earliest,required_asof)
    rows=list(csv.DictReader(io.StringIO(bound(root,name).read_text(encoding='utf-8-sig'))))
    if (record.get('target')!=target or observed.tzinfo is None
            or not earliest<=observed<=datetime.now(timezone.utc)
            or record.get('cache_sha256')!=sha(bound(root,name).read_bytes())
            or record.get('native_source_sha256')!=sha(bound(root,'microcap_top100_mom16_biweekly_live_v2_0.py').read_bytes().replace(b'\r\n',b'\n'))
            or not rows or record.get('count')!=len(rows)
            or len({r['code'] for r in rows})!=len(rows)):
        raise ValueError('Current-name native snapshot identity or observation differs')
    for key in ('st_observed_at','universe_observed_at'):
        stamp=datetime.fromisoformat(record[key])
        if stamp.tzinfo is None or not earliest<=stamp<=observed:
            raise ValueError('Current name sources do not share the required fresh observation window')
    universe=CACHE+'active_universe.csv'
    if (record.get('schema')!='current_publication_names_v2'
            or record.get('universe_sha256')!=sha(bound(root,universe).read_bytes())):
        raise ValueError('Current publication universe observation is missing or changed')
    count=validate_publication_names(root)
    if record.get('universe_count')!=count:raise ValueError('Current publication name coverage differs')
    if record.get('publication_name_scope')!='maintained_plus_ST_conflicts':
        raise ValueError('Current publication names require an explicit maintained-pool scope')
    files=[name,receipt,universe]
    if record.get('publication_name_scope')=='maintained_plus_ST_conflicts':
        witness=record['scoped_observation'];prior=witness['prior_universe']
        if prior['path']!=CACHE+'local_current_names/observations/'+prior['sha256']+'.universe.csv':
            raise ValueError('Original publication name snapshot has another file role')
        previous=bound(root,prior['path']).read_bytes()
        if sha(previous)!=prior['sha256']:raise ValueError('Original publication name snapshot changed')
        old=pd_read_names(previous);quotes={r['code']:r for r in witness['quote_rows']}
        import re
        conflicts={r['code'] for r in rows if re.fullmatch('[036][0-9]{5}',r['code']) and old.get(r['code'])!=r['name'].strip()}
        if (len(witness['maintained_symbols'])!=300 or len(set(witness['maintained_symbols']))!=300
                or (symbols is not None and set(witness['maintained_symbols'])!=set(symbols))
                or set(quotes)!=set(witness['requested_symbols']) or len(quotes)!=len(witness['quote_rows'])
                or set(witness['conflict_symbols'])!=conflicts or len(witness['conflict_symbols'])!=len(conflicts)
                or set(quotes)!=set(witness['maintained_symbols'])|set(witness['conflict_symbols'])):
            raise ValueError('Scoped publication name observation coverage differs')
        for row in quotes.values():
            fetched=datetime.fromisoformat(row['fetched_at'])
            if (fetched.tzinfo is None or not earliest<=fetched<=observed
                    or row.get('quote_source')!='tencent_batch_free' or not row.get('name','').strip()):
                raise ValueError('Scoped native name quote is stale or has an unknown source')
        old.update({s:r['name'].strip() for s,r in quotes.items()})
        if old!=pd_read_names(bound(root,universe).read_bytes()):
            raise ValueError('Publication names changed outside the witnessed scope')
        files.append(prior['path'])
    return hashes(root,files)


def validate_publication_names(root):
    rows=list(csv.DictReader(io.StringIO(bound(root,CACHE+'active_universe.csv').read_text(encoding='utf-8-sig'))))
    current=list(csv.DictReader(io.StringIO(bound(root,CACHE+'current_st.csv').read_text(encoding='utf-8-sig'))))
    mapping={r['code']:r['name'].strip() for r in rows}
    import re
    if (not rows or len(mapping)!=len(rows) or any(not re.fullmatch('[0-9]{6}',s) or not n for s,n in mapping.items())
            or not current or any(r['code'] not in mapping or mapping[r['code']]!=r['name'].strip()
                for r in current if re.fullmatch('[036][0-9]{5}',r['code']))):
        raise ValueError('Current name and ST snapshots disagree; refresh the original lightweight name sources')
    return len(rows)


def refresh_current_names(root, target, native_fetch, native_universe_fetch, *, provider_trace=None,scoped_observation=None):
    # Keep immutable prior observations before the target's current pointer is replaced.
    current=bound(root,CACHE+'current_st.csv')
    receipt=bound(root,CACHE+'local_current_names/'+target+'.json')
    if current.exists() and receipt.exists():
        originals=[('.csv',current.read_bytes()),('.json',receipt.read_bytes())]
        universe=bound(root,CACHE+'active_universe.csv')
        if universe.exists():originals.append(('.universe.csv',universe.read_bytes()))
        for suffix,raw in originals:
            saved=bound(root,CACHE+'local_current_names/observations/'+sha(raw)+suffix)
            if saved.exists() and saved.read_bytes()!=raw: raise ValueError('Immutable current-name observation collision')
            if not saved.exists(): atomic(saved,raw)
    native_fetch(force_refresh=True)
    st_observed=datetime.now(timezone.utc).isoformat()
    native_universe_fetch(force_refresh=True)
    universe_observed=datetime.now(timezone.utc).isoformat()
    universe_count=validate_publication_names(root)
    raw=bound(root,CACHE+'current_st.csv').read_bytes()
    rows=list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
    atomic(bound(root,CACHE+'local_current_names/'+target+'.json'),encode(dict(
        schema='current_publication_names_v2',target=target,observed_at=datetime.now(timezone.utc).isoformat(),count=len(rows),
        st_observed_at=st_observed,universe_observed_at=universe_observed,
        native_functions=['fetch_current_st_codes','fetch_tencent_realtime_quotes' if scoped_observation is not None else 'fetch_active_universe'],provider_trace=provider_trace or [],
        publication_name_scope='maintained_plus_ST_conflicts' if scoped_observation is not None else 'full_native_name_snapshot',
        scoped_observation=scoped_observation,
        cache_sha256=sha(raw),native_source_sha256=sha(bound(root,
            'microcap_top100_mom16_biweekly_live_v2_0.py').read_bytes().replace(b'\r\n',b'\n')),
        universe_count=universe_count,universe_sha256=sha(bound(root,CACHE+'active_universe.csv').read_bytes()),
        semantics='native_current_name_cross_check_not_PIT_ST_certification')))


def refresh_native_current_names(root,target,native,symbols,quote_fetch):
    from scripts import bounded_market_data as market
    original=market.fetch_akshare_frame;trace=[]
    def observed(method,*args,**kwargs):
        item=dict(method=method,started_at=datetime.now(timezone.utc).isoformat());trace.append(item)
        try:
            frame=original(method,*args,**kwargs)
            item.update(status='complete',rows=len(frame),frame_sha256=sha(frame.to_csv(index=False).encode('utf-8')))
            return frame
        except Exception as exc:
            item.update(status='failed',error_type=type(exc).__name__)
            raise
        finally:item['completed_at']=datetime.now(timezone.utc).isoformat()
    witness={}
    def scoped_universe(*,force_refresh):
        if force_refresh is not True:raise ValueError('Fresh scoped name observations are required')
        path=bound(root,CACHE+'active_universe.csv')
        if not path.exists():native.fetch_active_universe(force_refresh=True)
        previous=path.read_bytes();old=pd_read_names(previous)
        current=list(csv.DictReader(io.StringIO(bound(root,CACHE+'current_st.csv').read_text(encoding='utf-8-sig'))))
        import re
        conflicts=sorted(r['code'] for r in current if re.fullmatch('[036][0-9]{5}',r['code']) and old.get(r['code'])!=r['name'].strip())
        requested=sorted(set(symbols)|set(conflicts))
        if len(symbols)!=300 or len(set(symbols))!=300:raise ValueError('Scoped name refresh requires the maintained 300')
        frame=quote_fetch(requested)
        rows=json.loads(frame.to_json(orient='records',date_format='iso',force_ascii=False))
        quotes={r['code']:r for r in rows}
        if set(quotes)!=set(requested) or len(quotes)!=len(rows) or any(not r.get('name','').strip() for r in rows):
            raise ValueError('Native scoped name quotes are incomplete; keep the prior publication map')
        expected=dict(old);expected.update({s:r['name'].strip() for s,r in quotes.items()})
        if any(expected.get(r['code'])!=r['name'].strip() for r in current if re.fullmatch('[036][0-9]{5}',r['code'])):
            raise ValueError('Independent native name quotes disagree with the ST board')
        prior=CACHE+'local_current_names/observations/'+sha(previous)+'.universe.csv'
        if not bound(root,prior).exists():atomic(bound(root,prior),previous)
        witness.update(maintained_symbols=sorted(symbols),conflict_symbols=conflicts,requested_symbols=requested,
            quote_rows=rows,prior_universe=dict(path=prior,sha256=sha(previous)))
        original_rows=list(csv.DictReader(io.StringIO(previous.decode('utf-8-sig'))))
        for row in original_rows:row['name']=expected[row['code']]
        original_rows += [dict(symbol=('sh' if s.startswith('6') else 'sz')+s,name=expected[s],code=s)
                          for s in requested if s not in old]
        stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=['symbol','name','code']);writer.writeheader();writer.writerows(original_rows)
        atomic(path,stream.getvalue().encode('utf-8'))
    market.fetch_akshare_frame=observed
    try:refresh_current_names(root,target,native.fetch_current_st_codes,scoped_universe,provider_trace=trace,scoped_observation=witness)
    finally:market.fetch_akshare_frame=original


def pd_read_names(raw):
    rows=list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
    mapping={row['code']:row['name'].strip() for row in rows}
    if not rows or len(rows)!=len(mapping):raise ValueError('Cached publication names have duplicate or missing codes')
    return mapping


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('bootstrap', 'run'))
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--protected-root', type=Path, required=True)
    p.add_argument('--state-root', type=Path, required=True)
    p.add_argument('--pool', type=Path, required=True)
    p.add_argument('--as-of', required=True)
    p.add_argument('--notice-source-root', type=Path)
    p.add_argument('--resume-notice-generation', type=Path)
    args = p.parse_args(argv)
    root, state = args.root.resolve(), args.state_root.resolve()
    actual_runtime=validate_runtime(root, args.protected_root)
    if not root.is_relative_to(state) or state == root:
        raise ValueError('Persistent runtime must be inside its dedicated state root')
    roster, symbols, scope_sha = load_pool(args.pool, args.as_of)
    sys.path.insert(0, str(root))
    if args.action == 'bootstrap':
        print(json.dumps(bootstrap(root, args.protected_root.resolve(), symbols, state,runtime_sha=actual_runtime))[:250])
        return 0
    if actual_runtime!=RUNTIME:
        raise ValueError('Active maintenance requires the current reviewed runtime; legacy P2 is read-only bootstrap compatibility')
    import pandas as pd
    import microcap_top100_mom16_biweekly_live_v2_0 as v2
    from scripts.exchange_calendar import latest_completed_session, is_trading_day
    from scripts.st_notice_refresh import refresh_notice_archive, freshness_source_files, FRESHNESS
    from scripts.st_history_scope import source_closure, resolve_archive_reference
    from scripts.stock_input_contract import source_fingerprint
    target = date.fromisoformat(args.as_of)
    if target > latest_completed_session() or not is_trading_day(target):
        raise ValueError('Maintenance target is not a completed exchange session')
    if not (state / 'bootstrap.json').exists(): raise ValueError('Bootstrap receipt missing')
    if Path(v2.base_mod.freq_mod.ROOT).resolve() != root: raise ValueError('Native frequency root differs')
    from microcap_local_workers import run_workers
    floor=observation_floor()
    if datetime.now(timezone.utc)<floor: raise ValueError('Local morning maintenance is not due before 09:00')
    context = dict(runtime_sha=actual_runtime, native_sources=source_fingerprint(root),
                   observation_owner_date=floor.date().isoformat(), observation_floor=floor.isoformat(),
                   orchestrator_sources={name: sha((Path(__file__).parent / name).read_bytes())
                      for name in ('maintain_microcap_local.py', 'microcap_local_workers.py', 'prepare_microcap_local.py','microcap_local_http.py')},
                   orchestrator_sha256=sha(Path(__file__).read_bytes()), target=args.as_of,
                   scope_sha256=scope_sha, master_sha256=sha(bound(root, CACHE + 'security_master.csv').read_bytes()))
    with writer_lock(state / 'writer.lock'):
        initialization = bootstrap_coverage(json.loads((state / 'bootstrap.json').read_bytes()), symbols)
        journal = Journal(state / 'runs' / args.as_of, root, context)
        def notice_verify():
            proof = json.loads(bound(root, FRESHNESS).read_bytes())
            if proof['target'] != args.as_of: raise ValueError('Notice authority target differs')
            if datetime.fromisoformat(proof['required_asof'])<floor:
                raise ValueError('Notice observation predates this local morning')
            return hashes(root, [FRESHNESS, *freshness_source_files(root)])
        def notice_action():
            if bound(root, FRESHNESS).exists():
                try: notice_verify(); return
                except ValueError: pass
            select_local_notice_forward(root=root,protected_root=args.protected_root,state=state,symbols=symbols,
                target=args.as_of,out=state/'runs'/args.as_of/'local-notice-forward',writer_locked=True)
            if args.notice_source_root:
                import_notice_evidence(args.notice_source_root, root, args.as_of)
            else:
                refresh_notice_with_existing_pages(root=root,target=args.as_of,required_asof=floor,
                    out=state/'runs'/args.as_of/'notice-generation-admissions',refresh=refresh_notice_archive,
                    resume_generation=args.resume_notice_generation)
        notice = journal.run('notice', notice_action, notice_verify)
        current_names=journal.run('current-names',lambda:refresh_native_current_names(root,args.as_of,v2.base_mod.fetch_mod,
            symbols,v2.base_mod.fetch_tencent_realtime_quotes),
            lambda:current_names_proof(root,args.as_of,floor,symbols))
        # Native approved Tencent route has explicit suspension/status evidence.
        # No empty-response fallback or inferred suspension is introduced here.
        os.environ.setdefault('MICROCAP_DAILY_STOCK_PROVIDER', 'tencent')
        # The pinned source-only reader requires the original target proof, one
        # exact reviewed plan and a new independent observation-day corroboration.
        # This opts collection into that narrow protocol, never an as-of waiver.
        os.environ.setdefault('MICROCAP_SHARE_LATER_QUOTE_CORROBORATION', '1')
        progress = run_workers(root, state, symbols, context)
        progress['notice_status'] = notice['status']
        failures = progress['failures']
        # ST uses the unchanged native staged transaction over this checkout's
        # actual 300-stock cache. The frozen notice source is authenticated once
        # per batch and rechecked before any metadata is installed.
        available = symbols
        class ScopedFrequency:
            def __getattr__(self, name): return getattr(v2.base_mod.freq_mod, name)
            def list_backtest_universe_symbols(self): return available
        freq_scope = ScopedFrequency()
        def metadata_verify():
            require_complete_bootstrap(json.loads((state / 'bootstrap.json').read_bytes()), symbols)
            freq = v2.base_mod.freq_mod
            if not available or not set(available) <= set(freq.list_backtest_universe_symbols()):
                raise ValueError('Scoped metadata requires the initialized explicit roster')
            for s in available:
                meta = freq._read_security_meta_payload(bound(root, CACHE + 'security_meta/' + s + '.json'), s)
                if (not meta.get('notice_source_complete') or meta['st_notice_archive_sha256'] != resolve_archive_reference(root)[1]
                        or not freq._notice_horizon_covers(meta, pd.Timestamp(args.as_of))):
                    raise ValueError('Scoped metadata authority/horizon differs: ' + s)
            return hashes(root, [CACHE + 'security_meta/' + s + '.json' for s in available] +
                          [CACHE + 'st_notice_archive_pointer.json',CACHE+'current_st.csv'])
        def metadata_action():
            from scripts import st_metadata_refresh as metadata_native
            baseline = json.loads((state / 'bootstrap.json').read_bytes())
            require_complete_bootstrap(baseline, symbols)
            if sha((state / 'reference-proxy.csv').read_bytes()) != baseline['reference_proxy_sha256']:
                raise ValueError('Reference consumed proxy changed')
            original_marker = metadata_native.MARKER
            try:
                # A completed transaction belongs to one roster, while raw
                # source history can remain cached across weekly roster changes.
                metadata_native.MARKER = scoped_metadata_marker(root,scope_sha)
                metadata_native.refresh_metadata_after_notice(root=root, freq_mod=freq_scope, target=args.as_of,
                    notice_result={'authority_changed': True}, consumed_through=baseline['reference_consumed_through'],
                    scoped_preparation=True)
            finally:
                metadata_native.MARKER = original_marker
        metadata = journal.run('metadata', metadata_action, metadata_verify) if notice['status'] == current_names['status'] == 'complete' else {'status': 'waiting_for_notice_or_current_names'}
        progress.update(status='complete' if not failures and notice['status'] == current_names['status'] == metadata['status'] == 'complete' else 'incomplete',
                        completed_at=datetime.now(timezone.utc).isoformat(), context=context,
                        symbols=symbols, bootstrap_status=initialization['status'],
                        missing_initialized_symbols=initialization['missing_symbols'],
                        metadata_status=metadata['status'],current_names_status=current_names['status'], full_strategy_delivery=False, email_sent=False, orders_placed=False)
        atomic(state / 'runs' / args.as_of / 'result.json', encode(progress))
        print(json.dumps({k:v for k,v in progress.items() if k != 'failures'}))
        return 0 if progress['status'] == 'complete' else 1


if __name__ == '__main__': raise SystemExit(main())
