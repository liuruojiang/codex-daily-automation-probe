"""Failure isolation, verified recovery and scope/producer identity boundaries."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import maintain_microcap_local as local


def test_successful_issuer_survives_another_failure_and_only_failed_stage_runs(tmp_path):
    root = tmp_path / 'runtime';root.mkdir()
    journal = local.Journal(tmp_path / 'journal', root, {'target': '2026-11-06'})
    calls = []
    def stage(symbol, fail=False):
        def act():
            calls.append(symbol)
            if fail: raise TimeoutError('request failed; no placeholder publication')
            (root / (symbol + '.json')).write_text(symbol)
        def verify(): return local.hashes(root, [symbol + '.json'])
        return journal.run(symbol, act, verify)
    assert stage('a')['status'] == 'complete'
    assert stage('b', fail=True)['status'] == 'failed'
    assert stage('c')['status'] == 'complete'
    assert stage('a')['status'] == 'complete'
    assert stage('b')['status'] == 'complete'
    assert stage('c')['status'] == 'complete'
    assert calls == ['a', 'b', 'c', 'b']
    assert journal.reused == 2


def test_controller_change_revalidates_native_evidence_without_network(tmp_path):
    root = tmp_path / 'runtime';root.mkdir()
    (root / 'receipt').write_bytes(b'authentic existing source')
    def prohibited(): pytest.fail('unnecessary network refresh')
    for revision in ('old', 'fixed'):
        j = local.Journal(tmp_path / 'journal', root, {'controller': revision})
        assert j.run('price', prohibited, lambda: local.hashes(root, ['receipt']))['status'] == 'complete'


def test_completed_source_is_semantically_verified_once_and_hash_read_back(tmp_path):
    (tmp_path/'data').write_bytes(b'authenticated source')
    calls=[]
    def verify():
        calls.append(True)
        return local.hashes(tmp_path,['data'])
    j=local.Journal(tmp_path/'journal',tmp_path,{'date':'2026-11-06'})
    assert j.run('price',lambda:pytest.fail('unnecessary refresh'),verify)['status']=='complete'
    assert len(calls)==1


def test_native_scoped_metadata_marker_has_an_existing_parent(tmp_path):
    relative=local.scoped_metadata_marker(tmp_path,'a'*64)
    path=local.bound(tmp_path,relative)
    assert path.parent.is_dir() and not path.exists()
    path.write_bytes(b'native transaction marker')
    assert local.scoped_metadata_marker(tmp_path,'a'*64)==relative
    assert path.read_bytes()==b'native transaction marker'


def test_current_names_use_native_force_refresh_and_preserve_observation_receipt(tmp_path):
    source=tmp_path/'microcap_top100_mom16_biweekly_live_v2_0.py'
    source.write_bytes(b'# source identity fixture')
    calls=[]
    def fetch(**kwargs):
        calls.append(kwargs)
        path=local.bound(tmp_path,local.CACHE+'current_st.csv');path.parent.mkdir(parents=True)
        path.write_bytes(b'code,name\n000001,ST example\n')
    def universe(**kwargs):
        calls.append(kwargs)
        local.bound(tmp_path,local.CACHE+'active_universe.csv').write_bytes(b'code,name\n000001,ST example\n')
    local.refresh_current_names(tmp_path,'2000-01-04',fetch,universe)
    assert calls==[{'force_refresh':True},{'force_refresh':True}]
    with pytest.raises(ValueError,match='explicit maintained-pool scope'):
        local.current_names_proof(tmp_path,'2000-01-04')
    from datetime import datetime,timezone,timedelta
    with pytest.raises(ValueError):local.current_names_proof(tmp_path,'2000-01-04',datetime.now(timezone.utc)+timedelta(seconds=1))
    old_names=(tmp_path/local.CACHE/'current_st.csv').read_bytes()
    old_receipt=local.bound(tmp_path,local.CACHE+'local_current_names/2000-01-04.json').read_bytes()
    local.refresh_current_names(tmp_path,'2000-01-04',lambda **kwargs:None,lambda **kwargs:None)
    snapshots=local.bound(tmp_path,local.CACHE+'local_current_names/observations')
    assert (snapshots/(local.sha(old_names)+'.csv')).read_bytes()==old_names
    assert (snapshots/(local.sha(old_receipt)+'.json')).read_bytes()==old_receipt
    (tmp_path/local.CACHE/'current_st.csv').write_bytes(b'changed')
    with pytest.raises((ValueError,KeyError)):local.current_names_proof(tmp_path,'2000-01-04')


@pytest.mark.parametrize('fault',['future','target','code','universe_clock','universe_bytes','conflicting_name'])
def test_current_name_receipt_cannot_be_redated_or_reused_with_unknown_source(tmp_path,fault):
    source=tmp_path/'microcap_top100_mom16_biweekly_live_v2_0.py';source.write_bytes(b'original')
    name=local.bound(tmp_path,local.CACHE+'current_st.csv');name.parent.mkdir(parents=True)
    name.write_bytes(b'code,name\n000001,ST example\n')
    local.bound(tmp_path,local.CACHE+'active_universe.csv').write_bytes(b'code,name\n000001,ST example\n')
    local.refresh_current_names(tmp_path,'2000-01-04',lambda **kw:None,lambda **kw:None)
    receipt=local.bound(tmp_path,local.CACHE+'local_current_names/2000-01-04.json')
    value=json.loads(receipt.read_bytes())
    if fault=='future':value['observed_at']='2099-01-01T00:00:00+00:00'
    elif fault=='target':value['target']='2000-01-05'
    elif fault=='code':source.write_bytes(b'unknown source')
    elif fault=='universe_clock':value['universe_observed_at']='1999-01-01T00:00:00+00:00'
    elif fault=='universe_bytes':local.bound(tmp_path,local.CACHE+'active_universe.csv').write_bytes(b'changed')
    else:
        raw=b'code,name\n000001,ordinary name\n'
        local.bound(tmp_path,local.CACHE+'active_universe.csv').write_bytes(raw)
        value['universe_sha256']=local.sha(raw)
    receipt.write_bytes(local.encode(value))
    with pytest.raises(ValueError):local.current_names_proof(tmp_path,'2000-01-04')


def test_atomic_temporary_suffix_keeps_windows_extended_path(tmp_path):
    import os
    if os.name!='nt':pytest.skip('Win32 temporary-path regression')
    length=240-len(str(tmp_path.resolve()))-len('/files//receipt.json')
    path=local.bound(tmp_path,'files/'+'x'*length+'/receipt.json')
    local.atomic(path,b'original verified data')
    assert path.read_bytes()==b'original verified data'


@pytest.mark.parametrize('fault',[None,'runtime','role','source'])
def test_preparation_runtime_requires_the_pinned_role_and_source_bytes(tmp_path,monkeypatch,fault):
    code=tmp_path/'orchestrator';(code/'scripts').mkdir(parents=True);(code/'config').mkdir()
    root=tmp_path/'runtime';root.mkdir();protected=tmp_path/'protected';protected.mkdir()
    (root/'native.py').write_bytes(b'original native fixture')
    cfg=dict(schema='microcap_local_source_runtime_v1',runtime_sha=local.RUNTIME,
             role='source_preparation_only',max_candidates=300,
             calculation_sources={'native.py':local.sha(b'original native fixture')})
    if fault=='runtime':cfg['runtime_sha']='0'*40
    elif fault=='role':cfg['role']='formal_deployment'
    elif fault=='source':(root/'native.py').write_bytes(b'changed code')
    (code/'config/microcap_local_producer_runtime.json').write_bytes(local.encode(cfg))
    monkeypatch.setattr(local,'__file__',str(code/'scripts/maintain_microcap_local.py'))
    monkeypatch.setattr(local.subprocess,'check_output',lambda args,**kw:local.RUNTIME+'\n' if 'rev-parse' in args else '')
    if fault is None:local.validate_runtime(root,protected)
    else:
        with pytest.raises(ValueError):local.validate_runtime(root,protected)


def test_corrupt_completed_stage_does_not_refresh_over_the_evidence(tmp_path):
    root = tmp_path / 'runtime';root.mkdir()
    (root / 'data').write_bytes(b'correct')
    expected = local.sha(b'correct')
    def verify():
        result = local.hashes(root, ['data'])
        if result['data'] != expected: raise ValueError('native data identity failed')
        return result
    j = local.Journal(tmp_path / 'journal', root, {'date': '2026-11-06'})
    assert j.run('issuer-price', lambda: None, verify)['status'] == 'complete'
    (root / 'data').write_bytes(b'corrupt')
    result = j.run('issuer-price', lambda: pytest.fail('must preserve corrupt evidence'), verify)
    assert result['status'] == 'failed' and result['error_type'] == 'ValueError'
    assert (root / 'data').read_bytes() == b'corrupt'
    # An independent issuer remains runnable.
    assert j.run('other', lambda: None, lambda: local.hashes(root, ['data']))['status'] == 'complete'


def pool(tmp_path):
    rows = {'candidates': [dict(symbol=f'{n:06d}', current_member=n <= 100) for n in range(1, 301)]}
    (tmp_path / 'candidates.json').write_bytes(local.encode(rows))
    summary = dict(schema='microcap_local_screen_preparation_v1', status='PREPARATION_VERIFIED',
                   date='2026-11-06', run_directory=str(tmp_path),
                   candidates_sha256=local.sha((tmp_path / 'candidates.json').read_bytes()))
    p = tmp_path / 'latest.json';p.write_bytes(local.encode(summary))
    return p


@pytest.mark.parametrize('target', ['2026-11-05', '2026-11-14'])
def test_pool_from_future_or_older_than_week_is_rejected(tmp_path, target):
    with pytest.raises(ValueError, match='stale or from the future'):
        local.load_pool(pool(tmp_path), target)


def test_weekly_pool_keeps_its_dated_binding_and_members(tmp_path):
    p = pool(tmp_path)
    _, symbols, _ = local.load_pool(p, '2026-11-09')
    assert len(symbols) == 300
    (tmp_path / 'candidates.json').write_bytes(b'[]')
    with pytest.raises(ValueError, match='binding'): local.load_pool(p, '2026-11-09')


@pytest.mark.parametrize('relative', ['../outside', '/absolute'])
def test_source_paths_cannot_escape_runtime(tmp_path, relative):
    with pytest.raises(ValueError): local.bound(tmp_path, relative)


def test_deep_native_recovery_source_paths_roundtrip_without_changing_identity(tmp_path):
    relative=local.CACHE+'share_sources/providers/dual_source_dated_window_v1/002566/'+('a'*64)+'/transport_recovery_'+('b'*64)+'/quote.raw'
    path=local.bound(tmp_path,relative);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(b'actual source bytes fixture')
    assert local.hashes(tmp_path,[relative])=={relative:local.sha(b'actual source bytes fixture')}
    assert local.bound(tmp_path,relative).read_bytes()==b'actual source bytes fixture'


def test_bound_trade_evidence_rejects_content_mismatch(tmp_path):
    (tmp_path / 'raw.json').write_bytes(b'raw')
    ref = {'path': 'raw.json', 'sha256': local.sha(b'raw')}
    assert local.read_bound_refs({'provider_queries': [ref]}, tmp_path) == {'raw.json': local.sha(b'raw')}
    (tmp_path / 'raw.json').write_bytes(b'different')
    with pytest.raises(ValueError): local.read_bound_refs(ref, tmp_path)


def test_tail_anchor_is_read_from_exact_immutable_original_price_snapshot(tmp_path):
    name = local.CACHE + 'return_basis/bundles/000001/verified/raw.csv'
    path = local.bound(tmp_path, name);path.parent.mkdir(parents=True)
    raw = b'date,close_raw\n2026-11-02,10.0\n2026-11-05,10.5\n'
    path.write_bytes(raw)
    baseline = {'files': {local.CACHE + 'prices_raw/000001.csv': local.sha(raw), name: local.sha(raw)}}
    assert local.price_seed_anchor(tmp_path, '000001', baseline) == '2026-11-05'
    path.write_bytes(raw + b'2026-11-06,10.6\n')
    with pytest.raises(ValueError, match='seed changed'):
        local.price_seed_anchor(tmp_path, '000001', baseline)
