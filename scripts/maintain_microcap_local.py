"""Scoped native stock-input maintenance in a dedicated persistent checkout.

No NAV, strategy authority, signal, SMTP or orders are produced here. Historical
calculations retain their original full universe. Each successful source stage
is verified and recorded separately; another issuer's failure cannot erase it.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout, redirect_stderr
import csv
from datetime import date, datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback
import uuid

from prepare_microcap_local import atomic, encode, sha, writer_lock

RUNTIME = 'c8d15e418c7003021866cd2aa4984e3e9a7103b6'
BOOTSTRAP_RUNTIMES = {RUNTIME, 'a4adeacca194d5add4cfea0456c27e9da5444e45'}
CACHE = '.microcap_index_cache/'


def bound(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(root):
        raise ValueError('Source path escaped the declared root')
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
    if actual != RUNTIME:
        raise ValueError('Native maintenance runtime differs from the reviewed exact commit')
    dirty = subprocess.check_output(['git', '-C', str(root), 'diff', '--name-only', 'HEAD'], text=True).splitlines()
    if any(not name.startswith((CACHE, 'outputs/')) for name in dirty):
        raise ValueError('Native runtime has executable edits')


def load_pool(path, target):
    path = Path(path)
    summary = json.loads(path.read_bytes())
    if summary.get('schema') != 'microcap_local_screen_preparation_v1' or summary.get('status') != 'PREPARATION_VERIFIED':
        raise ValueError('Scoped maintenance requires a verified preparation roster')
    if not 0 <= (date.fromisoformat(target) - date.fromisoformat(summary['date'])).days <= 7:
        raise ValueError('Weekly preparation roster is stale or from the future')
    pool_path = Path(summary['run_directory']) / 'candidates.json'
    raw = pool_path.read_bytes()
    if sha(raw) != summary['candidates_sha256']:
        raise ValueError('Candidate roster binding differs')
    pool = json.loads(raw)
    symbols = [r['symbol'] for r in pool['candidates']]
    if len(symbols) != 300 or len(set(symbols)) != 300 or sum(r['current_member'] for r in pool['candidates']) != 100:
        raise ValueError('Scoped roster must contain 300 unique issuers and 100 current members')
    return summary, sorted(symbols), sha(raw)


def bootstrap(root, source, symbols, state):
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
        result = dict(status='partial' if failures else 'complete', runtime_sha=RUNTIME, source_root=str(source),
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


def current_names_proof(root, target):
    """Current name cross-check only; native notice history still proves PIT ST."""
    name=CACHE+'current_st.csv';receipt=CACHE+'local_current_names/'+target+'.json'
    record=json.loads(bound(root,receipt).read_bytes())
    observed=datetime.fromisoformat(record['observed_at'])
    earliest=datetime.fromisoformat(target+'T15:00:00+08:00')
    rows=list(csv.DictReader(io.StringIO(bound(root,name).read_text(encoding='utf-8-sig'))))
    if (record.get('target')!=target or observed.tzinfo is None
            or not earliest<=observed<=datetime.now(timezone.utc)
            or record.get('cache_sha256')!=sha(bound(root,name).read_bytes())
            or record.get('native_source_sha256')!=sha(bound(root,'microcap_top100_mom16_biweekly_live_v2_0.py').read_bytes().replace(b'\r\n',b'\n'))
            or not rows or record.get('count')!=len(rows)
            or len({r['code'] for r in rows})!=len(rows)):
        raise ValueError('Current-name native snapshot identity or observation differs')
    return hashes(root,[name,receipt])


def refresh_current_names(root, target, native_fetch):
    native_fetch(force_refresh=True)
    raw=bound(root,CACHE+'current_st.csv').read_bytes()
    rows=list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
    atomic(bound(root,CACHE+'local_current_names/'+target+'.json'),encode(dict(
        target=target,observed_at=datetime.now(timezone.utc).isoformat(),count=len(rows),
        cache_sha256=sha(raw),native_source_sha256=sha(bound(root,
            'microcap_top100_mom16_biweekly_live_v2_0.py').read_bytes().replace(b'\r\n',b'\n')),
        semantics='native_current_name_cross_check_not_PIT_ST_certification')))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('bootstrap', 'run'))
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--protected-root', type=Path, required=True)
    p.add_argument('--state-root', type=Path, required=True)
    p.add_argument('--pool', type=Path, required=True)
    p.add_argument('--as-of', required=True)
    p.add_argument('--notice-source-root', type=Path)
    args = p.parse_args(argv)
    root, state = args.root.resolve(), args.state_root.resolve()
    validate_runtime(root, args.protected_root)
    if not root.is_relative_to(state) or state == root:
        raise ValueError('Persistent runtime must be inside its dedicated state root')
    roster, symbols, scope_sha = load_pool(args.pool, args.as_of)
    sys.path.insert(0, str(root))
    if args.action == 'bootstrap':
        print(json.dumps(bootstrap(root, args.protected_root.resolve(), symbols, state))[:250])
        return 0
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
    context = dict(runtime_sha=RUNTIME, native_sources=source_fingerprint(root),
                   orchestrator_sources={name: sha((Path(__file__).parent / name).read_bytes())
                      for name in ('maintain_microcap_local.py', 'microcap_local_workers.py', 'prepare_microcap_local.py')},
                   orchestrator_sha256=sha(Path(__file__).read_bytes()), target=args.as_of,
                   scope_sha256=scope_sha, master_sha256=sha(bound(root, CACHE + 'security_master.csv').read_bytes()))
    with writer_lock(state / 'writer.lock'):
        journal = Journal(state / 'runs' / args.as_of, root, context)
        def notice_verify():
            proof = json.loads(bound(root, FRESHNESS).read_bytes())
            if proof['target'] != args.as_of: raise ValueError('Notice authority target differs')
            return hashes(root, [FRESHNESS, *freshness_source_files(root)])
        def notice_action():
            if bound(root, FRESHNESS).exists():
                try: notice_verify(); return
                except ValueError: pass
            if args.notice_source_root:
                import_notice_evidence(args.notice_source_root, root, args.as_of)
            else:
                refresh_notice_archive(root=root, target=args.as_of)
        notice = journal.run('notice', notice_action, notice_verify)
        current_names=journal.run('current-names',lambda:refresh_current_names(root,args.as_of,
            v2.base_mod.fetch_mod.fetch_current_st_codes),lambda:current_names_proof(root,args.as_of))
        # Native approved Tencent route has explicit suspension/status evidence.
        # No empty-response fallback or inferred suspension is introduced here.
        os.environ.setdefault('MICROCAP_DAILY_STOCK_PROVIDER', 'tencent')
        progress = run_workers(root, state, symbols, context)
        progress['notice_status'] = notice['status']
        failures = progress['failures']
        # ST uses the unchanged native staged transaction over this checkout's
        # actual 300-stock cache. The frozen notice source is authenticated once
        # per batch and rechecked before any metadata is installed.
        available = sorted(set(symbols) & set(json.loads((state / 'bootstrap.json').read_bytes())['initialized_symbols']))
        class ScopedFrequency:
            def __getattr__(self, name): return getattr(v2.base_mod.freq_mod, name)
            def list_backtest_universe_symbols(self): return available
        freq_scope = ScopedFrequency()
        def metadata_verify():
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
            if sha((state / 'reference-proxy.csv').read_bytes()) != baseline['reference_proxy_sha256']:
                raise ValueError('Reference consumed proxy changed')
            original_marker = metadata_native.MARKER
            try:
                # A completed transaction belongs to one roster, while raw
                # source history can remain cached across weekly roster changes.
                metadata_native.MARKER = scoped_metadata_marker(root,scope_sha)
                metadata_native.refresh_metadata_after_notice(root=root, freq_mod=freq_scope, target=args.as_of,
                    notice_result={'authority_changed': True}, consumed_through=baseline['reference_consumed_through'])
            finally:
                metadata_native.MARKER = original_marker
        metadata = journal.run('metadata', metadata_action, metadata_verify) if notice['status'] == current_names['status'] == 'complete' else {'status': 'waiting_for_notice_or_current_names'}
        progress.update(status='complete' if not failures and notice['status'] == current_names['status'] == metadata['status'] == 'complete' else 'incomplete',
                        completed_at=datetime.now(timezone.utc).isoformat(), context=context,
                        symbols=symbols,
                        metadata_status=metadata['status'],current_names_status=current_names['status'], full_strategy_delivery=False, email_sent=False, orders_placed=False)
        atomic(state / 'runs' / args.as_of / 'result.json', encode(progress))
        print(json.dumps({k:v for k,v in progress.items() if k != 'failures'}))
        return 0 if progress['status'] == 'complete' else 1


if __name__ == '__main__': raise SystemExit(main())
