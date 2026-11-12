"""Adopt hash-pinned local source caches into a source-only producer.

This explicit route never changes a full consumer, NAV, ST metadata or authority.
Its consumer must still earn the original complete old-state/adoption gates.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, ExitStack
import csv
from datetime import date
from decimal import Decimal
import io
import json
from pathlib import Path
import re
import socket
import sys

import numpy as np
import pandas as pd

from prepare_microcap_local import sha, writer_lock
from maintain_microcap_local import (CACHE, RUNTIME, SOURCE_RUNTIMES, bound, hashes, load_pool, validate_runtime,
                                    price_proof, share_proof, ratio_proof)
from microcap_scoped_inputs import read_bundle, apply_inputs, preserve_history

PROXY='outputs/wind_microcap_top_100_biweekly_thursday_16y_cached.csv'
FORMAL='outputs/top100_delivery_manifest.json'
P2_RUNTIME='3fe2e15ee9e3f2ee7e25bbc328479f1fd9910d36'
P3_RUNTIME='3344e403ff01e17ef45b745a704a240e0917720b'
P2_P3_READERS={
    'scripts/share_change_source.py': (
        'be9e393f09196a812d60b9eb867d21e32b6afbd1e7862d7605accb8bf809d93c',
        'fdbc6292597ced431cf76bfdd9ad564c70042f8d603ee867f79d5da2bed2339e'),
    'scripts/share_unchanged_tail.py': (
        'd5824797c483d0b466684a5bebbe19595b7108fb393cd7815dc7b870688dd7ae',
        '80ca426b78cfda4e63fc32be0157be188ebbe31d68e2f9758664b2d7d2342491'),
}


def producer_profile(name, runtime):
    value=json.loads((Path(__file__).resolve().parents[1]/'config'/name).read_bytes())
    sources=value.get('calculation_sources')
    if (value.get('schema')!='microcap_local_source_runtime_v1' or value.get('role')!='source_preparation_only'
            or value.get('runtime_sha')!=runtime or value.get('max_candidates')!=300
            or not isinstance(sources,dict) or len(sources)!=18
            or any(re.fullmatch('[0-9a-f]{64}',str(v)) is None for v in sources.values())):
        raise ValueError('Frozen producer reader profile differs')
    return value


def exact_p2_successor(source_runtime,sources,current,reader_runtime):
    """Read original P2 inputs with this exact P3 reader, never resign inputs."""
    old=producer_profile('microcap_local_producer_p2_20261112.json',P2_RUNTIME)['calculation_sources']
    new=producer_profile('microcap_local_producer_runtime.json',P3_RUNTIME)['calculation_sources']
    changed={name for name in old if old.get(name)!=new.get(name)}
    if (source_runtime!=P2_RUNTIME or reader_runtime!=P3_RUNTIME or set(old)!=set(new)
            or sources!=old or current!=new or changed!=set(P2_P3_READERS)
            or any((old[name],new[name])!=pair for name,pair in P2_P3_READERS.items())):
        raise ValueError('Unreviewed P2 to P3 source-only reader successor')
    return dict(schema='exact_p2_p3_source_reader_v1',source_runtime=P2_RUNTIME,reader_runtime=P3_RUNTIME,
                source_fingerprints=old,reader_fingerprints=new,changed_sources=sorted(changed),
                original_source_identity_preserved=True,native_proof_required=True)


@contextmanager
def offline():
    """Defence against accidental networking in any delegated native reader."""
    original=(socket.create_connection,socket.getaddrinfo,socket.socket.connect)
    def forbidden(*a,**k):raise ValueError('Producer source import permits no network access')
    socket.create_connection=socket.getaddrinfo=socket.socket.connect=forbidden
    try:yield
    finally:socket.create_connection,socket.getaddrinfo,socket.socket.connect=original


def history(raw,key,value):
    rows=list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
    pairs=[(r[key][:10],Decimal(r[value])) for r in rows]
    if (not pairs or [d for d,_ in pairs]!=sorted({d for d,_ in pairs})
            or any(not v.is_finite() or v<=0 for _,v in pairs)):
        raise ValueError('Producer source history is empty, duplicated, unordered or nonpositive')
    for day,_ in pairs:date.fromisoformat(day)
    return pairs


def preserve_returns(before,after,cutoff):
    # Same comparison as native scoped_source_consumer.preserve_return_history.
    # The preparation-only producer predates that consumer module.
    def series(raw):
        frame=pd.read_csv(io.BytesIO(raw))
        if not {'date','close_adj'}<=set(frame):raise ValueError('Adjusted history schema differs')
        dates=pd.to_datetime(frame.date,errors='raise');values=pd.to_numeric(frame.close_adj,errors='raise')
        if (dates.isna().any() or dates.duplicated().any() or not dates.is_monotonic_increasing
                or not np.isfinite(values).all() or values.le(0).any()):
            raise ValueError('Adjusted history is invalid')
        return pd.Series(values.to_numpy(),index=dates).loc[:pd.Timestamp(cutoff)]
    old,new=series(before),series(after)
    if not old.index.equals(new.index):raise ValueError('Producer import changes observed adjusted-date coverage')
    np.testing.assert_allclose(old.pct_change(fill_method=None).to_numpy(),new.pct_change(fill_method=None).to_numpy(),
        rtol=0,atol=8*np.finfo(float).eps,equal_nan=True,err_msg='Producer import changes observed stock returns')


def mutable_files(symbol):
    files=[CACHE+folder+'/'+symbol+suffix for folder,suffix in (
        ('prices_raw','.csv'),('share_change','.csv'),('share_sources','.json'),('trade_status','.json'),
        ('return_basis/manifests','.json'),('return_basis/prices_adj','.csv'))]
    return files+[CACHE+'return_basis/sources/'+symbol+'/'+name for name in ('qfq.js','hfq.js','qfq.json','hfq.json')]


def assert_native_modules(root,source_files):
    # An API caller may previously have imported the full consumer. Do not
    # silently validate a producer using another checkout's cached modules.
    names={name[:-3].replace('/','.') : root/name for name in source_files if name.endswith('.py')}
    names['microcap_top100_mom16_biweekly_live_v2_0']=root/'microcap_top100_mom16_biweekly_live_v2_0.py'
    for name,path in names.items():
        loaded=sys.modules.get(name)
        if loaded is not None and Path(getattr(loaded,'__file__','')).resolve()!=path:
            raise ValueError('Native modules belong to another checkout; run producer import in an isolated process')


def checked_bootstrap(root,protected,state,symbols,not_after):
    paths=[state/'bootstrap.json',state/'reference-proxy.csv',protected/PROXY,protected/FORMAL]
    raw={path:path.read_bytes() for path in paths};record=json.loads(raw[paths[0]])
    initialized=record.get('initialized_symbols',[])
    if (record.get('status')!='complete' or Path(record.get('source_root','')).resolve()!=protected
            or set(record.get('symbols',[]))!=set(symbols) or not isinstance(initialized,list)
            or any(not isinstance(s,str) or re.fullmatch('[0-9]{6}',s) is None for s in initialized)
            or len(initialized)!=len(set(initialized)) or not set(symbols)<=set(initialized)
            or record.get('failures') or len(record.get('symbols',[]))!=300):
        raise ValueError('Producer import requires its real complete protected-root bootstrap')
    reference=raw[paths[1]];digest=sha(reference)
    if digest!=record.get('reference_proxy_sha256') or raw[paths[2]]!=reference:
        raise ValueError('Producer reference differs from the actual protected proxy archive')
    rows=list(csv.DictReader(io.StringIO(reference.decode('utf-8-sig'))))
    if not rows:raise ValueError('Protected proxy reference is empty')
    anchor=max(r['date'][:10] for r in rows);date.fromisoformat(anchor)
    formal=json.loads(raw[paths[3]]);expected={**formal.get('inputs',{}),**formal.get('artifacts',{})}.get(PROXY)
    if (record.get('reference_consumed_through')!=anchor or anchor>not_after
            or formal.get('ok') is not True or formal.get('status')!='complete'
            or formal.get('scope')!='whole_workspace_delivery' or formal.get('expected_date')!=anchor
            or expected not in (sha(reference),sha(reference.replace(b'\r\n',b'\n')))):
        raise ValueError('Bootstrap reference is not bound to the actual formal protected proxy')
    return raw


def plan(root,manifest,bodies):
    target=manifest['target'];symbols=manifest['symbols'];originals={};anchors={};newer={}
    mutable={name for symbol in symbols for name in mutable_files(symbol)}
    if not mutable<=set(manifest['files']):raise ValueError('Producer source package lacks complete issuer baselines')
    for name in manifest['files']:
        path=bound(root,name);previous=path.read_bytes() if path.exists() else None
        if name in mutable:
            if previous is None:raise ValueError('Producer issuer baseline is missing: '+name)
            originals[name]=previous
        elif previous is not None and previous!=bodies[name]:
            raise ValueError('Producer import conflicts with immutable source evidence: '+name)
    for symbol in symbols:
        names=mutable_files(symbol);raw,share,share_state,status,basis,adjusted=names[:6]
        observed=history(originals[raw],'date','close_raw')[-1][0];anchors[symbol]=observed
        if (history(bodies[raw],'date','close_raw')[-1][0]>target
                or history(bodies[adjusted],'date','close_adj')[-1][0]>target
                or json.loads(bodies[share_state])['query_end']!=target
                or json.loads(bodies[basis])['as_of']!=target):
            raise ValueError('Incoming producer source pointers do not belong to their declared target')
        history(originals[share],'change_date','total_shares_10k');history(bodies[share],'change_date','total_shares_10k')
        query=json.loads(originals[share_state])['query_end'];date.fromisoformat(query)
        certified=json.loads(originals[basis])['as_of'];date.fromisoformat(certified)
        adjusted_end=history(originals[adjusted],'date','close_adj')[-1][0]
        # Preserve observations beyond the last raw close during suspension.
        # A request's end may include future/closed days; it is not itself an
        # observation horizon and must not suppress a valid local warm cache.
        status_end=[]
        for spec in json.loads(originals[status]).get('provider_queries',[]):
            path=bound(root,CACHE+'trade_status/'+spec['path']);body=path.read_bytes()
            if sha(body)!=spec['sha256']:raise ValueError('Existing trade-status evidence changed')
            payload=json.loads(body);fields=payload['fields'];position=fields.index('date')
            for row in payload['rows']:
                observed_day=row[position];date.fromisoformat(observed_day);status_end.append(observed_day)
        horizon=max([observed,query,certified,adjusted_end,*status_end])
        if horizon>target:newer[symbol]=horizon;continue
        preserve_history(originals[raw],bodies[raw],'date','close_raw',observed)
        preserve_history(originals[share],bodies[share],'change_date','total_shares_10k',query)
        preserve_returns(originals[adjusted],bodies[adjusted],max(certified,adjusted_end))
    return originals,anchors,newer


def import_producer(*,root,protected_root,bundle,expected_sha,not_after,pool,state=None):
    root,protected=Path(root).resolve(),Path(protected_root).resolve()
    state=Path(state).resolve() if state else root.parent
    if root==state or not root.is_relative_to(state):raise ValueError('Producer must be below its declared state root')
    if state==protected or state.is_relative_to(protected) or protected.is_relative_to(state):
        raise ValueError('Producer state must be separate from the protected full consumer')
    if not isinstance(expected_sha,str) or re.fullmatch('[0-9a-f]{64}',expected_sha) is None:
        raise ValueError('Producer import requires an exact source bundle SHA256')
    date.fromisoformat(not_after)
    actual_runtime=validate_runtime(root,protected)
    if (root/'outputs').exists() and any((root/'outputs').rglob('*')):
        raise ValueError('Producer source import refuses any strategy outputs')
    _,symbols,scope=load_pool(pool,not_after)
    manifest,bodies,digest=read_bundle(bundle,not_after,expected_sha)
    context=manifest.get('producer_context',{})
    if (manifest.get('runtime_sha') not in SOURCE_RUNTIMES or context.get('runtime_sha')!=manifest.get('runtime_sha')
            or manifest.get('scope_sha256')!=scope or context.get('scope_sha256')!=scope
            or set(manifest['symbols'])!=set(symbols) or context.get('target')!=manifest['target']):
        raise ValueError('Producer source package runtime, date or verified roster differs')
    sys.path.insert(0,str(root))
    from scripts.stock_input_contract import source_fingerprint,SOURCE_FILES
    current=source_fingerprint(root)
    assert_native_modules(root,SOURCE_FILES)
    master_name=CACHE+'security_master.csv';master_raw=bound(root,master_name).read_bytes()
    if (sha(master_raw)!=manifest.get('master_sha256') or bodies['maintenance-evidence/master.csv']!=master_raw
            or context.get('master_sha256')!=sha(master_raw)):
        raise ValueError('Producer package changes the bootstrapped historical master')
    with ExitStack() as stack:
        for path in (state/'writer.lock',state/'worker-slot-0.lock',state/'worker-slot-1.lock',bound(root,CACHE+'local_input_import.lock')):
            stack.enter_context(writer_lock(path))
        references=checked_bootstrap(root,protected,state,symbols,not_after)
        common=dict(source_preparation_only=True,source_target=manifest['target'],issuer_count=300,
                    bundle_sha256=digest,final_strategy_delivery=False,production_pointers_changed=False,
                    native_consumer_gate_required=True,email_sent=False,orders_placed=False)
        reader=None
        if manifest['runtime_sha']!=RUNTIME:
            baseline=json.loads(references[state/'bootstrap.json'])['reference_consumed_through']
            if manifest['target']<=baseline:
                return dict(common,status='skipped_source_covered_by_formal_baseline',
                            formal_baseline=baseline,source_runtime=manifest['runtime_sha'],producer_pointers_changed=False)
            reader=exact_p2_successor(manifest['runtime_sha'],context.get('native_sources'),current,actual_runtime)
        if (set(context.get('native_sources',{}))!=set(SOURCE_FILES)
                or (reader is None and context['native_sources']!=current)):
            raise ValueError('Producer package calculation source identities differ')
        if reader is not None:
            common.update(source_runtime=manifest['runtime_sha'],validation_reader=reader)
        originals,anchors,newer=plan(root,manifest,bodies)
        if newer:return dict(common,status='skipped_newer_producer_baseline',newer_symbols=newer,producer_pointers_changed=False)
        def verify():
            with offline():
                import microcap_top100_mom16_biweekly_live_v2_0 as v2
                assert_native_modules(root,SOURCE_FILES)
                if Path(v2.base_mod.freq_mod.ROOT).resolve()!=root:
                    raise ValueError('Native producer frequency root differs')
                master=v2.base_mod.freq_mod.load_security_master()
                for symbol in symbols:
                    price_proof(root,symbol,manifest['target'],master,anchors[symbol])
                    share_proof(root,symbol,manifest['target'])
                    ratio_proof(root,symbol,manifest['target'],master)
                assert_native_modules(root,SOURCE_FILES)
            if any(path.read_bytes()!=raw for path,raw in references.items()) or bound(root,master_name).read_bytes()!=master_raw:
                raise ValueError('Protected bootstrap reference changed during producer import')
            verified_runtime=validate_runtime(root,protected)
            if actual_runtime is not None and verified_runtime!=actual_runtime:
                raise ValueError('Producer reader runtime changed during import')
        if all(bound(root,name).is_file() for name in manifest['files']) and hashes(root,manifest['files'])==manifest['files']:
            verify()
            return dict(common,status='skipped_same_verified_producer_inputs',producer_pointers_changed=False)
        if any(bound(root,name).read_bytes()!=raw for name,raw in originals.items()):
            raise ValueError('Producer baseline changed before import')
        receipt=apply_inputs(root,manifest,bodies,verify)
        return dict(common,status='producer_source_inputs_imported',producer_pointers_changed=True,receipt=str(receipt))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('root','protected-root','bundle','pool'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--state',type=Path);parser.add_argument('--sha256',required=True);parser.add_argument('--not-after-date',required=True)
    args=parser.parse_args()
    result=import_producer(root=args.root,protected_root=args.protected_root,bundle=args.bundle,expected_sha=args.sha256,
                           not_after=args.not_after_date,pool=args.pool,state=args.state)
    print(json.dumps(result))


if __name__=='__main__':main()
