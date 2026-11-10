"""Portable verified source inputs for the existing native rebalance consumer.

This transport excludes NAVs, executable code and ST authority. The rebalance
consumer must still refresh/validate its own native ST and complete delivery.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date
from decimal import Decimal
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import uuid
import zipfile

from prepare_microcap_local import atomic, encode, sha, writer_lock
from maintain_microcap_local import CACHE, RUNTIME, SOURCE_RUNTIMES, bound, hashes
from microcap_local_due import completed

SCHEMA = 'microcap_scoped_source_inputs_v1'
PREFIXES = tuple(CACHE + p + '/' for p in ('prices_raw', 'share_change', 'trade_status', 'share_sources', 'return_basis'))
MAX_BYTES = 1024 * 1024 * 1024
REVIEWED_CONSUMERS = frozenset({'33062463d66afbff0c4342e3dd9203fa3117d865',
    '564d681308dd7593ffadb075d4dd5291e4c4e89c','6b56e6902799aa189a0bbc42c18d6506dcc2a04e'})


def validate_consumer(root, manifest, actual):
    """Keep producer identity separate from an immutable, reviewed reader."""
    if actual not in SOURCE_RUNTIMES and actual not in REVIEWED_CONSUMERS:
        raise ValueError('Scoped source consumer requires the exact reviewed native runtime')
    if actual in REVIEWED_CONSUMERS:
        from scripts.top100_delivery import verify_candidate_release
        from scripts.scoped_source_consumer import validate_source_contract
        verify_candidate_release(root, actual)
        validate_source_contract(root, manifest)
    else:
        from scripts.stock_input_contract import source_fingerprint
        if source_fingerprint(root) != manifest['producer_context']['native_sources']:
            raise ValueError('Scoped input calculation source differs at consumer')


def permitted(name):
    p = PurePosixPath(name)
    baseline_binary=(len(p.parts)==5 and p.parts[:3]==('.microcap_index_cache','share_sources','baselines')
        and len(p.parts[3])==64 and all(c in '0123456789abcdef' for c in p.parts[3])
        and p.parts[4]=='accepted_issuer_evidence.bin')
    return (isinstance(name, str) and '\\' not in name and not p.is_absolute()
            and '..' not in p.parts and ':' not in name and p.as_posix() == name
            and name.startswith(PREFIXES) and (p.suffix in ('.json', '.csv', '.js', '.raw') or baseline_binary)
            and not any(part in ('locks', 'incomplete', 'failures') for part in p.parts))


def export(root, state, target, bundle):
    root, state, bundle = Path(root), Path(state), Path(bundle)
    files = {}
    directory = state / 'runs' / target
    if not completed(directory, root, target, files_out=files):
        raise ValueError('A complete hash-verified 300-stock source run is required')
    result = json.loads((directory / 'result.json').read_bytes())
    if result.get('context',{}).get('runtime_sha')!=RUNTIME:
        raise ValueError('Completed source run belongs to another native runtime; revalidate its sources')
    for name,digest in result['context']['native_sources'].items():
        if sha(bound(root,name).read_bytes().replace(b'\r\n',b'\n'))!=digest:
            raise ValueError('Native source code changed after completed maintenance')
    if any(p.startswith(PREFIXES) and not permitted(p) for p in files):
        raise ValueError('A native source evidence object has no supported transport role')
    files = {p:h for p,h in files.items() if permitted(p)}
    symbols = result['symbols']
    for s in symbols:
        for group, suffix in (('prices_raw','.csv'),('share_change','.csv'),('share_sources','.json'),
                              ('trade_status','.json'),('return_basis/manifests','.json'),('return_basis/prices_adj','.csv')):
            if CACHE + group + '/' + s + suffix not in files:
                raise ValueError('Transport lacks native issuer closure: ' + s)
    producer_commit=subprocess.check_output(['git','-C',str(Path(__file__).resolve().parents[1]),'rev-parse','HEAD'],text=True).strip()
    manifest = dict(schema=SCHEMA, runtime_sha=RUNTIME, target=target,producer_commit=producer_commit,
                    producer_context=result['context'], symbols=symbols, files=files,
                    scope_sha256=result['scope_sha256'], source_inputs_only=True,
                    native_ST_refresh_required_at_consumer=True, final_strategy_delivery=False,
                    master_sha256=sha(bound(root, CACHE+'security_master.csv').read_bytes()))
    extras = {'maintenance-evidence/master.csv':bound(root,CACHE+'security_master.csv').read_bytes()}
    manifest['evidence'] = {name:sha(raw) for name,raw in extras.items()}
    bundle.parent.mkdir(parents=True, exist_ok=True)
    if bundle.exists(): raise ValueError('Refuse to overwrite an existing source bundle')
    with writer_lock(state/'writer.lock'), zipfile.ZipFile(bundle, 'x', zipfile.ZIP_DEFLATED) as archive:
        if hashes(root, files) != files: raise ValueError('Source inputs changed before export')
        for name,digest in files.items():
            raw=bound(root,name).read_bytes()
            if sha(raw)!=digest: raise ValueError('Source input changed during export')
            archive.writestr(name, raw)
        for name,raw in extras.items(): archive.writestr(name,raw)
        archive.writestr('manifest.json', encode(manifest))
    _, _, digest = read_bundle(bundle, target)
    return dict(status='source_inputs_exported', bundle_sha256=digest, target=target,
                issuer_count=300, files=len(files), final_strategy_delivery=False)


def read_bundle(bundle, not_after, expected_sha=None):
    bundle=Path(bundle)
    if not 0 < bundle.stat().st_size <= MAX_BYTES: raise ValueError('Scoped input archive size is invalid')
    raw=bundle.read_bytes();digest=sha(raw)
    if expected_sha is not None and digest!=expected_sha: raise ValueError('Scoped input archive SHA differs')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names=archive.namelist()
        if len(names)!=len(set(names)) or 'manifest.json' not in names: raise ValueError('Duplicate or absent archive manifest')
        if sum(i.file_size for i in archive.infolist())>MAX_BYTES: raise ValueError('Scoped input archive expands beyond limit')
        manifest=json.loads(archive.read('manifest.json'))
        if (manifest.get('schema')!=SCHEMA or manifest.get('runtime_sha') not in SOURCE_RUNTIMES
                or manifest.get('source_inputs_only') is not True
                or manifest.get('native_ST_refresh_required_at_consumer') is not True
                or manifest.get('final_strategy_delivery') is not False):
            raise ValueError('Scoped input producer identity or scope differs')
        target=date.fromisoformat(manifest['target'])
        if target>date.fromisoformat(not_after): raise ValueError('Scoped source inputs are from a future session')
        symbols=manifest['symbols']
        if len(symbols)!=300 or len(set(symbols))!=300 or any(not isinstance(s,str) or len(s)!=6 or not s.isascii() or not s.isdigit() for s in symbols):
            raise ValueError('Scoped archive requires 300 unique issuer codes')
        if any(not permitted(p) for p in manifest['files']): raise ValueError('Scoped archive contains an unsupported file role')
        for name in manifest['files']:
            parts=PurePosixPath(name).parts
            if parts[1] in ('prices_raw','share_change') and (len(parts)!=3 or Path(parts[2]).stem not in symbols):
                raise ValueError('Scoped archive writes outside its declared issuers')
            if parts[1] in ('trade_status','share_sources') and len(parts)==3 and Path(parts[2]).stem.isdigit() and Path(parts[2]).stem not in symbols:
                raise ValueError('Scoped archive changes another issuer pointer')
            if parts[1]=='return_basis' and (len(parts)<4 or
                (Path(parts[3]).stem if parts[2] in ('manifests','prices_adj') else parts[3]) not in symbols):
                raise ValueError('Return basis writes outside declared issuer scope')
        if set(manifest.get('evidence',{}))!={'maintenance-evidence/master.csv'}:
            raise ValueError('Scoped archive master evidence role differs')
        expected={'manifest.json',*manifest['files'],*manifest['evidence']}
        if set(names)!=expected: raise ValueError('Undeclared or missing archive entries')
        bodies={name:archive.read(name) for name in names if name!='manifest.json'}
        for name,h in {**manifest['files'],**manifest['evidence']}.items():
            if sha(bodies[name])!=h: raise ValueError('Scoped source object hash differs')
        if sha(bodies['maintenance-evidence/master.csv'])!=manifest['master_sha256']:
            raise ValueError('Source master evidence hash differs')
    return manifest,bodies,digest


def preserve_history(before, after, key, field, boundary):
    def values(raw):
        return {r[key][:10]:Decimal(r[field]) for r in csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))}
    old,new=values(before),values(after)
    dates=sorted({d for d in set(old)|set(new) if d<=boundary})
    left=right=None
    for d in dates:
        if key=='date':
            if old.get(d)!=new.get(d): raise ValueError('Scoped import rewrites or deletes historical raw prices')
        else:
            left=old.get(d,left);right=new.get(d,right)
            if left!=right: raise ValueError('Scoped import changes previously certified share history')


def apply_inputs(root, manifest, bodies, verify_native):
    root=Path(root).resolve()
    work=bound(root,CACHE+'local_input_imports/'+uuid.uuid4().hex)
    work.mkdir(parents=True)
    originals={name:bound(root,name).read_bytes() if bound(root,name).exists() else None for name in manifest['files']}
    for name,raw in originals.items():
        if raw is not None: atomic(work/'before'/name, raw)
    attempted=[]
    try:
        for name in manifest['files']:
            current=bound(root,name)
            if (current.read_bytes() if current.exists() else None)!=originals[name]: raise ValueError('Concurrent input change before import')
            attempted.append(name);atomic(current,bodies[name])
        verify_native()
        if hashes(root,manifest['files'])!=manifest['files']: raise ValueError('Imported source closure changed during native readback')
    except BaseException:
        for name in reversed(attempted):
            path=bound(root,name);previous=originals[name]
            if not path.exists() or path.read_bytes() not in (previous,bodies[name]):
                raise ValueError('Concurrent import mutation prevents rollback')
            if previous is None:
                # Leave the new authenticated immutable object; rollback current
                # pointers below makes it unreachable. Never delete evidence.
                continue
            atomic(path,previous)
        atomic(work/'receipt.json',encode(dict(status='rolled_back_existing_inputs',new_immutable_objects_retained=True)))
        raise
    atomic(work/'receipt.json',encode(dict(status='native_sources_verified',target=manifest['target'],
                                         final_strategy_delivery=False)))
    return work/'receipt.json'


def stage_inputs(root, manifest, bodies, identity, trusted_files, verify_native):
    """Read back a candidate without changing the consumed global inputs.

    Legacy proxy metadata binds all installed issuer pointers. Prewarming them
    before the native old-state gate would invalidate that gate. Keep this
    source-only candidate below a separate directory until a reviewed native
    consumer can adopt the requested subset after its old-state verification.
    """
    root=Path(root).resolve()
    directory=bound(root,CACHE+'local_input_imports/'+identity)
    if os.name=='nt' and not str(directory).startswith('\\\\?\\'):
        directory=Path('\\\\?\\'+str(directory))
    candidate=directory/'root'
    for name,raw in {**trusted_files,**{n:bodies[n] for n in manifest['files']}}.items():
        path=bound(candidate,name)
        if path.exists() and path.read_bytes()!=raw:
            raise ValueError('Staged source object conflicts with preserved candidate')
        if not path.exists():atomic(path,raw)
    verify_native(candidate)
    if hashes(candidate,manifest['files'])!=manifest['files']:
        raise ValueError('Staged source closure changed during native readback')
    receipt=directory/'receipt.json'
    atomic(directory/'manifest.json',encode(manifest))
    atomic(receipt,encode(dict(status='native_sources_staged',target=manifest['target'],
        files=manifest['files'],source_bundle_sha256=identity,
        production_pointers_changed=False,native_late_adoption_required=True,
        final_strategy_delivery=False)))
    return receipt


def install(root, bundle, expected_sha, not_after):
    root=Path(root).resolve()
    actual=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
    if actual not in SOURCE_RUNTIMES and actual not in REVIEWED_CONSUMERS:
        raise ValueError('Scoped source consumer requires the exact reviewed native runtime')
    manifest,bodies,digest=read_bundle(bundle,not_after,expected_sha)
    sys.path.insert(0,str(root))
    import microcap_top100_mom16_biweekly_live_v2_0 as v2
    from maintain_microcap_local import price_proof,share_proof,ratio_proof
    validate_consumer(root,manifest,actual)
    target=manifest['target'];symbols=manifest['symbols']
    master=v2.base_mod.freq_mod.load_security_master()
    source_rows={r['symbol']:r for r in csv.DictReader(io.StringIO(bodies['maintenance-evidence/master.csv'].decode('utf-8-sig')))}
    destination_rows={r['symbol']:r for r in csv.DictReader(io.StringIO(bound(root,CACHE+'security_master.csv').read_text(encoding='utf-8-sig')))}
    anchors={}
    mutable_set=set()
    for s in symbols:
        if s not in source_rows or s not in destination_rows or any(source_rows[s].get(k)!=destination_rows[s].get(k) for k in ('list_date','delist_date','exchange','board')):
            raise ValueError('Issuer lifecycle differs from source package: '+s)
        mutable=[CACHE+group+'/'+s+suffix for group,suffix in
                 (('prices_raw','.csv'),('share_change','.csv'),('share_sources','.json'),('trade_status','.json'),
                  ('return_basis/manifests','.json'),('return_basis/prices_adj','.csv'))]
        mutable += [CACHE+'return_basis/sources/'+s+'/'+n for n in ('qfq.js','hfq.js','qfq.json','hfq.json')]
        mutable_set.update(mutable)
        if any(not bound(root,p).is_file() for p in mutable):
            raise ValueError('Consumer has no complete installed issuer baseline: '+s)
        old_price=bound(root,CACHE+'prices_raw/'+s+'.csv').read_bytes()
        rows=list(csv.DictReader(io.StringIO(old_price.decode('utf-8-sig'))))
        anchor=max(r['date'][:10] for r in rows);anchors[s]=anchor
        old_share_state=json.loads(bound(root,CACHE+'share_sources/'+s+'.json').read_bytes())
        if anchor>target or old_share_state['query_end']>target:
            return dict(status='skipped_newer_inputs_present',source_target=target,final_strategy_delivery=False)
        preserve_history(old_price,bodies[CACHE+'prices_raw/'+s+'.csv'],'date','close_raw',anchor)
        preserve_history(bound(root,CACHE+'share_change/'+s+'.csv').read_bytes(),
                         bodies[CACHE+'share_change/'+s+'.csv'],'change_date','total_shares_10k',old_share_state['query_end'])
    for name in manifest['files']:
        path=bound(root,name)
        if name not in mutable_set and path.exists() and path.read_bytes()!=bodies[name]:
            raise ValueError('Scoped import conflicts with an immutable existing evidence object')
    def verify(candidate):
        for s in symbols:
            price_proof(candidate,s,target,master,anchors[s])
            share_proof(candidate,s,target);ratio_proof(candidate,s,target,master)
    # Static native review/approval objects come only from the exact checked-out
    # strategy code, never from an untrusted data archive.
    names=subprocess.check_output(['git','-C',str(root),'ls-files','data'],text=True).splitlines()
    trusted_files={n:bound(root,n).read_bytes() for n in names}
    trusted_files[CACHE+'security_master.csv']=bound(root,CACHE+'security_master.csv').read_bytes()
    old_inputs=hashes(root,mutable_set)
    with writer_lock(bound(root,CACHE+'local_input_import.lock')):
        receipt=stage_inputs(root,manifest,bodies,digest,trusted_files,verify)
        if hashes(root,mutable_set)!=old_inputs:
            raise ValueError('Installed source baseline changed during independent prewarm')
    return dict(status='native_scoped_sources_staged',source_target=target,issuer_count=300,
                bundle_sha256=digest,receipt=str(receipt),production_pointers_changed=False,
                native_late_adoption_required=True,native_ST_refresh_required=True,final_strategy_delivery=False)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('export','import'))
    p.add_argument('--root',type=Path,required=True);p.add_argument('--bundle',type=Path,required=True)
    p.add_argument('--state-root',type=Path);p.add_argument('--date',required=True)
    p.add_argument('--sha256')
    a=p.parse_args()
    if a.action=='export':
        if a.state_root is None:p.error('--state-root required for export')
        result=export(a.root,a.state_root,a.date,a.bundle)
    else:
        if not a.sha256:p.error('--sha256 required for import')
        result=install(a.root,a.bundle,a.sha256,a.date)
    print(json.dumps(result))


if __name__=='__main__':main()
