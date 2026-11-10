"""Source-only import contracts with small software issuer fixtures.

The real bundle reader, 300-issuer history plan and rollback run locally; native
proof calls are module-boundary spies, not production source/data acceptance.
"""
import copy
import json
from pathlib import Path
import socket
import sys
from types import ModuleType,SimpleNamespace
import zipfile

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import import_microcap_producer_inputs as importer
from prepare_microcap_local import encode,sha
from microcap_scoped_inputs import SCHEMA


@pytest.fixture
def setup(tmp_path,monkeypatch):
    state=tmp_path/'state';root=state/'runtime';root.mkdir(parents=True)
    protected=tmp_path/'consumer';(protected/'outputs').mkdir(parents=True)
    symbols=[str(i).zfill(6) for i in range(1,301)]
    pooldir=tmp_path/'roster';pooldir.mkdir()
    candidates=encode(dict(candidates=[dict(symbol=s,current_member=i<100) for i,s in enumerate(symbols)]))
    (pooldir/'candidates.json').write_bytes(candidates)
    pool=pooldir/'latest.json';pool.write_bytes(encode(dict(schema='microcap_local_screen_preparation_v1',
        status='PREPARATION_VERIFIED',date='2026-11-09',run_directory=str(pooldir),candidates_sha256=sha(candidates))))
    reference=b'date,close\n2026-11-10,100\n'
    (protected/importer.PROXY).write_bytes(reference)
    (protected/importer.FORMAL).write_bytes(encode(dict(ok=True,status='complete',scope='whole_workspace_delivery',
        expected_date='2026-11-10',inputs={importer.PROXY:sha(reference)},artifacts={})))
    (state/'reference-proxy.csv').write_bytes(reference)
    (state/'bootstrap.json').write_bytes(encode(dict(status='complete',source_root=str(protected),symbols=symbols,
        initialized_symbols=symbols,failures={},reference_proxy_sha256=sha(reference),reference_consumed_through='2026-11-10')))
    master=b'symbol,list_date,delist_date,exchange,board\n000001,1991-01-01,,SZ,main\n'
    masterpath=root/'.microcap_index_cache/security_master.csv';masterpath.parent.mkdir();masterpath.write_bytes(master)
    originals={};bodies={}
    for symbol in symbols:
        names=importer.mutable_files(symbol)
        before=[b'date,close_raw\n2026-11-09,100\n2026-11-10,101\n',b'change_date,total_shares_10k\n2026-11-09,1000\n',
            encode(dict(query_end='2026-11-10')),encode(dict(provider_queries=[])),encode(dict(as_of='2026-11-10')),
            b'date,close_adj\n2026-11-09,100\n2026-11-10,101\n',b'qfq old',b'hfq old',b'{}',b'{}']
        after=[b'date,close_raw\n2026-11-09,100\n2026-11-10,101\n2026-11-11,102\n',before[1],
            encode(dict(query_end='2026-11-11')),before[3],encode(dict(as_of='2026-11-11')),
            b'date,close_adj\n2026-11-09,200\n2026-11-10,202\n2026-11-11,204\n',b'qfq new',b'hfq new',b'{}',b'{}']
        for name,old,new in zip(names,before,after):
            path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(old)
            originals[name]=old;bodies[name]=new
    bodies['.microcap_index_cache/return_basis/bundles/'+symbols[0]+'/new-immutable/raw.csv']=bodies[importer.mutable_files(symbols[0])[0]]
    contract=ModuleType('scripts.stock_input_contract')
    contract.__file__=str(root/'scripts/stock_input_contract.py')
    contract.SOURCE_FILES=('scripts/stock_input_contract.py',)+tuple('scripts/native_source_'+str(i)+'.py' for i in range(17))
    fingerprints={name:sha(name.encode()) for name in contract.SOURCE_FILES}
    contract.source_fingerprint=lambda root:dict(fingerprints)
    monkeypatch.setitem(sys.modules,'scripts.stock_input_contract',contract)
    native=ModuleType('microcap_top100_mom16_biweekly_live_v2_0')
    native.__file__=str(root/'microcap_top100_mom16_biweekly_live_v2_0.py')
    native.base_mod=SimpleNamespace(freq_mod=SimpleNamespace(ROOT=root,load_security_master=lambda:object()))
    monkeypatch.setitem(sys.modules,'microcap_top100_mom16_biweekly_live_v2_0',native)
    roles=[]
    monkeypatch.setattr(importer,'validate_runtime',lambda root,protected:roles.append((root,protected)))
    calls=[];failure={'enabled':False,'network':False}
    def price(root,s,target,master,anchor):
        calls.append(('price',s,target,anchor))
        assert (root/importer.mutable_files(s)[0]).read_bytes()==bodies[importer.mutable_files(s)[0]]
        if failure['network']:socket.getaddrinfo('must-not-resolve.invalid',443)
    def share(root,s,target):
        calls.append(('share',s,target))
        if failure['enabled']:raise ValueError('native share proof failed')
    def ratio(root,s,target,master):calls.append(('ratio',s,target))
    monkeypatch.setattr(importer,'price_proof',price);monkeypatch.setattr(importer,'share_proof',share);monkeypatch.setattr(importer,'ratio_proof',ratio)
    manifest=dict(schema=SCHEMA,runtime_sha=importer.RUNTIME,target='2026-11-11',symbols=symbols,
        producer_context=dict(runtime_sha=importer.RUNTIME,target='2026-11-11',scope_sha256=sha(candidates),
                              native_sources=fingerprints,master_sha256=sha(master)),
        scope_sha256=sha(candidates),source_inputs_only=True,native_ST_refresh_required_at_consumer=True,
        final_strategy_delivery=False,master_sha256=sha(master),files={},evidence={'maintenance-evidence/master.csv':sha(master)})
    bundle=tmp_path/'source.zip'
    def package():
        manifest['files']={name:sha(raw) for name,raw in bodies.items()}
        with zipfile.ZipFile(bundle,'w',zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('manifest.json',encode(manifest))
            for name,raw in bodies.items():archive.writestr(name,raw)
            archive.writestr('maintenance-evidence/master.csv',master)
        return sha(bundle.read_bytes())
    args=dict(root=root,protected_root=protected,state=state,pool=pool,bundle=bundle,expected_sha=package(),not_after='2026-11-12')
    return SimpleNamespace(args=args,root=root,state=state,protected=protected,symbols=symbols,originals=originals,
                           bodies=bodies,manifest=manifest,package=package,calls=calls,failure=failure,roles=roles)


def assert_baseline_unchanged(setup):
    assert all((setup.root/name).read_bytes()==raw for name,raw in setup.originals.items())
    assert not (setup.root/'outputs').exists()


def test_verified_local_sources_advance_producer_only_after_900_native_readbacks(setup):
    protected={p:p.read_bytes() for p in (setup.protected/importer.PROXY,setup.protected/importer.FORMAL,setup.state/'bootstrap.json')}
    result=importer.import_producer(**setup.args)
    assert result['status']=='producer_source_inputs_imported' and result['producer_pointers_changed']
    assert result['source_preparation_only'] and not result['production_pointers_changed']
    assert result['native_consumer_gate_required'] and not result['final_strategy_delivery']
    assert len(setup.calls)==900 and all((setup.root/name).read_bytes()==raw for name,raw in setup.bodies.items())
    assert all(p.read_bytes()==raw for p,raw in protected.items())
    assert not result['email_sent'] and not result['orders_placed']


@pytest.mark.parametrize('fault',['raw_rewrite','raw_delete','raw_duplicate','share_rewrite','return_rewrite','return_delete',
    'scope','runtime','native_sources','master','incomplete_bootstrap','wrong_protected','reference_changed','unbound_proxy',
    'candidate_consumer','producer_has_outputs','incoming_future','immutable_collision'])
def test_bad_history_identity_or_scope_cannot_change_producer_inputs(setup,fault):
    raw,share,share_state,status,basis,adjusted=importer.mutable_files(setup.symbols[0])[:6]
    if fault=='raw_rewrite':setup.bodies[raw]=setup.bodies[raw].replace(b'2026-11-10,101',b'2026-11-10,99')
    elif fault=='raw_delete':setup.bodies[raw]=setup.bodies[raw].replace(b'2026-11-10,101\n',b'')
    elif fault=='raw_duplicate':setup.bodies[raw]+=b'2026-11-11,102\n'
    elif fault=='share_rewrite':setup.bodies[share]=setup.bodies[share].replace(b',1000',b',999')
    elif fault=='return_rewrite':setup.bodies[adjusted]=setup.bodies[adjusted].replace(b'2026-11-10,202',b'2026-11-10,203')
    elif fault=='return_delete':setup.bodies[adjusted]=setup.bodies[adjusted].replace(b'2026-11-10,202\n',b'')
    elif fault=='scope':setup.manifest['scope_sha256']='0'*64
    elif fault=='runtime':setup.manifest['runtime_sha']='0'*40
    elif fault=='native_sources':setup.manifest['producer_context']['native_sources'].pop(next(iter(setup.manifest['producer_context']['native_sources'])))
    elif fault=='master':(setup.root/'.microcap_index_cache/security_master.csv').write_bytes(b'different master')
    elif fault in ('incomplete_bootstrap','wrong_protected'):
        path=setup.state/'bootstrap.json';value=json.loads(path.read_bytes())
        if fault=='incomplete_bootstrap':value['status']='partial'
        else:value['source_root']=str(setup.root)
        path.write_bytes(encode(value))
    elif fault=='reference_changed':(setup.state/'reference-proxy.csv').write_bytes(b'changed')
    elif fault in ('unbound_proxy','candidate_consumer'):
        path=setup.protected/importer.FORMAL;value=json.loads(path.read_bytes())
        if fault=='unbound_proxy':value['inputs'][importer.PROXY]='0'*64
        else:value['scope']='whole_workspace_candidate_validation'
        path.write_bytes(encode(value))
    elif fault=='producer_has_outputs':
        path=setup.root/'outputs/top100_candidate_delivery_manifest.json';path.parent.mkdir();path.write_bytes(b'candidate')
    elif fault=='incoming_future':setup.bodies[raw]+=b'2026-11-12,103\n'
    else:
        name='.microcap_index_cache/return_basis/bundles/'+setup.symbols[0]+'/immutable/raw.csv'
        path=setup.root/name;path.parent.mkdir(parents=True);path.write_bytes(b'old immutable')
        setup.bodies[name]=b'different immutable'
    setup.args['expected_sha']=setup.package()
    with pytest.raises((ValueError,AssertionError)):importer.import_producer(**setup.args)
    assert all((setup.root/name).read_bytes()==raw for name,raw in setup.originals.items())
    assert not setup.calls


@pytest.mark.parametrize('which',['raw','share','basis','status'])
def test_newer_producer_knowledge_is_explicitly_skipped_without_downgrade(setup,which):
    names=importer.mutable_files(setup.symbols[0])
    if which=='raw':(setup.root/names[0]).write_bytes(setup.originals[names[0]]+b'2026-11-12,103\n')
    elif which=='share':(setup.root/names[2]).write_bytes(encode(dict(query_end='2026-11-12')))
    elif which=='basis':(setup.root/names[4]).write_bytes(encode(dict(as_of='2026-11-12')))
    else:
        path=setup.root/'.microcap_index_cache/trade_status/old-query.json';raw=encode(dict(request=dict(end='2026-11-12'),fields=['date'],rows=[['2026-11-12']]));path.write_bytes(raw)
        (setup.root/names[3]).write_bytes(encode(dict(provider_queries=[dict(path='old-query.json',sha256=sha(raw))])))
    before={name:(setup.root/name).read_bytes() for name in setup.originals}
    result=importer.import_producer(**setup.args)
    assert result['status']=='skipped_newer_producer_baseline' and not result['producer_pointers_changed']
    assert result['newer_symbols']=={setup.symbols[0]:'2026-11-12'}
    assert all((setup.root/name).read_bytes()==raw for name,raw in before.items()) and not setup.calls


def test_native_readback_failure_rolls_back_every_existing_source_pointer(setup):
    setup.failure['enabled']=True
    with pytest.raises(ValueError,match='native share proof'):importer.import_producer(**setup.args)
    assert_baseline_unchanged(setup)
    receipts=list((setup.root/'.microcap_index_cache/local_input_imports').glob('*/receipt.json'))
    assert any(json.loads(p.read_bytes())['status']=='rolled_back_existing_inputs' for p in receipts)


def test_accidental_network_attempt_is_blocked_and_source_changes_roll_back(setup):
    setup.failure['network']=True
    original=socket.getaddrinfo
    with pytest.raises(ValueError,match='no network access'):importer.import_producer(**setup.args)
    assert_baseline_unchanged(setup)
    assert socket.getaddrinfo is original


def test_identical_previously_imported_bundle_is_offline_verified_then_skipped(setup):
    importer.import_producer(**setup.args);setup.calls.clear()
    result=importer.import_producer(**setup.args)
    assert result['status']=='skipped_same_verified_producer_inputs'
    assert len(setup.calls)==900 and not result['producer_pointers_changed']


def test_changed_protected_archive_during_readback_refuses_and_rolls_back(setup,monkeypatch):
    original=importer.ratio_proof
    def changed(*args):
        original(*args)
        (setup.protected/importer.PROXY).write_bytes(b'concurrent protected update')
    monkeypatch.setattr(importer,'ratio_proof',changed)
    with pytest.raises(ValueError,match='reference changed'):importer.import_producer(**setup.args)
    assert_baseline_unchanged(setup)


def test_foreign_consumer_module_cache_cannot_validate_the_producer(setup):
    sys.modules['microcap_top100_mom16_biweekly_live_v2_0'].__file__=str(setup.protected/'microcap_top100_mom16_biweekly_live_v2_0.py')
    with pytest.raises(ValueError,match='isolated process'):importer.import_producer(**setup.args)
    assert_baseline_unchanged(setup)
    assert not setup.calls


def test_a_future_request_end_without_future_observations_does_not_discard_local_sources(setup):
    name=importer.mutable_files(setup.symbols[0])[3]
    path=setup.root/'.microcap_index_cache/trade_status/old-query.json'
    raw=encode(dict(request=dict(end='2026-12-31'),fields=['date'],rows=[['2026-11-10']]))
    path.write_bytes(raw)
    (setup.root/name).write_bytes(encode(dict(provider_queries=[dict(path='old-query.json',sha256=sha(raw))])))
    result=importer.import_producer(**setup.args)
    assert result['status']=='producer_source_inputs_imported' and len(setup.calls)==900


@pytest.fixture
def bootstrap_only(tmp_path):
    state=tmp_path/'state';root=state/'runtime';root.mkdir(parents=True)
    protected=tmp_path/'protected';(protected/'outputs').mkdir(parents=True)
    symbols=[str(i).zfill(6) for i in range(1,301)]
    reference=b'date,close\n2026-11-10,100\n'
    (state/'reference-proxy.csv').write_bytes(reference);(protected/importer.PROXY).write_bytes(reference)
    (protected/importer.FORMAL).write_bytes(encode(dict(ok=True,status='complete',scope='whole_workspace_delivery',
        expected_date='2026-11-10',inputs={importer.PROXY:sha(reference)},artifacts={})))
    record=dict(status='complete',source_root=str(protected),symbols=symbols,
        initialized_symbols=symbols+['300786','301027','600148','688078'],failures={},
        reference_proxy_sha256=sha(reference),reference_consumed_through='2026-11-10')
    return root,protected,state,symbols,record


def test_bootstrap_cumulative_304_retains_current_300_without_expanding_import_scope(bootstrap_only):
    root,protected,state,symbols,record=bootstrap_only
    path=state/'bootstrap.json';path.write_bytes(encode(record));original=path.read_bytes()
    checked=importer.checked_bootstrap(root,protected,state,symbols,'2026-11-12')
    assert checked[path]==original and path.read_bytes()==original
    assert len(record['initialized_symbols'])==304 and len(record['symbols'])==300


@pytest.mark.parametrize('fault',['duplicate_initialized','missing_current','invalid_retired','wrong_current'])
def test_cumulative_bootstrap_still_requires_unique_valid_initialized_current_members(bootstrap_only,fault):
    root,protected,state,symbols,record=bootstrap_only
    if fault=='duplicate_initialized':record['initialized_symbols'].append(record['initialized_symbols'][0])
    elif fault=='missing_current':record['initialized_symbols'].remove(symbols[0])
    elif fault=='invalid_retired':record['initialized_symbols'][-1]='not-stock'
    else:record['symbols'][0]='999999'
    (state/'bootstrap.json').write_bytes(encode(record))
    with pytest.raises(ValueError,match='complete protected-root bootstrap'):
        importer.checked_bootstrap(root,protected,state,symbols,'2026-11-12')
