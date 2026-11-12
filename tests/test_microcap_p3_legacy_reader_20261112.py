"""Exact old-source/new-reader transport; software issuers, no market or mail.

The existing real bundle/admission/history/rollback paths run against small
300-issuer fixtures. Native proof calls remain explicit module-boundary spies.
The optional local Git check independently reads the actual two producer blobs.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import import_microcap_producer_inputs as importer
import microcap_legacy_source_recovery as legacy
import restore_microcap_rebalance_recovery as resume
from prepare_microcap_local import encode,sha
from test_import_microcap_producer_inputs import setup as software_issuers
from test_microcap_legacy_source_recovery import originals,network


def profiles():
    old=importer.producer_profile('microcap_local_producer_p2_20261112.json',legacy.PRODUCER)['calculation_sources']
    new=importer.producer_profile('microcap_local_producer_runtime.json',legacy.READER_PRODUCER)['calculation_sources']
    return old,new


@pytest.fixture
def warm(software_issuers,monkeypatch):
    case=software_issuers;old,new=profiles()
    contract=sys.modules['scripts.stock_input_contract']
    contract.SOURCE_FILES=tuple(new)
    contract.source_fingerprint=lambda _root:dict(new)
    # These Native proof boundaries are software spies. Isolate every real
    # source name from prior recovery imports; monkeypatch restores the cache.
    for name in new:
        if name=='scripts/stock_input_contract.py' or not name.endswith('.py'):continue
        qualified=name[:-3].replace('/','.')
        module=ModuleType(qualified);module.__file__=str(case.root/name)
        monkeypatch.setitem(sys.modules,qualified,module)
    monkeypatch.setattr(importer,'validate_runtime',lambda *_a:legacy.READER_PRODUCER)
    case.manifest['runtime_sha']=legacy.PRODUCER
    case.manifest['producer_context'].update(runtime_sha=legacy.PRODUCER,native_sources=old)
    case.args['expected_sha']=case.package()
    return case


def test_real_git_P2_and_P3_sources_match_the_frozen_eighteen_file_review():
    root=Path(os.environ.get('MICROCAP_P3_GIT_ROOT',r'D:\Codex\workspaces\microcap-quote-fallback-p3-20261112'))
    if not root.is_dir():pytest.skip('Actual local producer Git checkout not supplied')
    old,new=profiles()
    for ref,expected in ((legacy.PRODUCER,old),(legacy.READER_PRODUCER,new)):
        actual={name:hashlib.sha256(subprocess.check_output(['git','-C',str(root),'show',ref+':'+name])
                .replace(b'\r\n',b'\n')).hexdigest() for name in expected}
        assert actual==expected
    proof=importer.exact_p2_successor(legacy.PRODUCER,old,new,legacy.READER_PRODUCER)
    assert proof['changed_sources']==sorted(importer.P2_P3_READERS)
    assert proof['original_source_identity_preserved'] is True


def test_P2_day11_warm_inputs_advance_P3_after_all_900_native_checks(warm):
    original_bundle=warm.args['bundle'].read_bytes()
    protected={p:p.read_bytes() for p in (warm.state/'bootstrap.json',warm.state/'reference-proxy.csv',
        warm.protected/importer.PROXY,warm.protected/importer.FORMAL)}
    result=importer.import_producer(**warm.args)
    assert result['status']=='producer_source_inputs_imported' and len(warm.calls)==900
    assert result['source_runtime']==legacy.PRODUCER
    assert result['validation_reader']['reader_runtime']==legacy.READER_PRODUCER
    assert warm.args['bundle'].read_bytes()==original_bundle
    assert all(p.read_bytes()==raw for p,raw in protected.items())
    assert not result['production_pointers_changed'] and not result['final_strategy_delivery']
    assert not result['email_sent'] and not result['orders_placed']


@pytest.mark.parametrize('fault',['source_hash','reader_hash','partial_pair','extra_source','reader_role','source_role','historical_rewrite'])
def test_only_the_exact_two_share_file_successor_can_import_a_newer_P2_package(warm,monkeypatch,fault):
    old,new=profiles()
    if fault=='source_hash':warm.manifest['producer_context']['native_sources']['scripts/share_unchanged_tail.py']='0'*64
    elif fault=='reader_hash':
        new['scripts/share_unchanged_tail.py']='0'*64
        sys.modules['scripts.stock_input_contract'].source_fingerprint=lambda _root:new
    elif fault=='partial_pair':
        new['scripts/share_change_source.py']=old['scripts/share_change_source.py']
        sys.modules['scripts.stock_input_contract'].source_fingerprint=lambda _root:new
    elif fault=='extra_source':warm.manifest['producer_context']['native_sources']['scripts/other.py']='0'*64
    elif fault=='reader_role':monkeypatch.setattr(importer,'validate_runtime',lambda *_a:legacy.PRODUCER)
    elif fault=='source_role':
        warm.manifest['runtime_sha']=legacy.READER_PRODUCER
        warm.manifest['producer_context']['runtime_sha']=legacy.READER_PRODUCER
    else:
        name=importer.mutable_files(warm.symbols[0])[0]
        warm.bodies[name]=warm.bodies[name].replace(b'2026-11-10,101',b'2026-11-10,99')
    warm.args['expected_sha']=warm.package()
    with pytest.raises((ValueError,AssertionError)):importer.import_producer(**warm.args)
    assert not warm.calls
    assert all((warm.root/name).read_bytes()==raw for name,raw in warm.originals.items())


def test_P2_warm_native_failure_rolls_back_without_upgrading_source_identity(warm):
    warm.failure['enabled']=True
    before=deepcopy(warm.manifest)
    with pytest.raises(ValueError,match='native share proof'):importer.import_producer(**warm.args)
    assert warm.manifest==before
    assert all((warm.root/name).read_bytes()==raw for name,raw in warm.originals.items())


def test_reader_role_change_during_native_readback_rolls_back_the_warm_import(warm,monkeypatch):
    roles=iter((legacy.READER_PRODUCER,legacy.PRODUCER))
    monkeypatch.setattr(importer,'validate_runtime',lambda *_a:next(roles))
    with pytest.raises(ValueError,match='reader runtime changed'):
        importer.import_producer(**warm.args)
    assert len(warm.calls)==900
    assert all((warm.root/name).read_bytes()==raw for name,raw in warm.originals.items())


def test_P2_package_already_covered_by_formal_baseline_keeps_original_skip(warm):
    warm.manifest['target']='2026-11-10';warm.manifest['producer_context']['target']='2026-11-10'
    warm.args['expected_sha']=warm.package()
    result=importer.import_producer(**warm.args)
    assert result['status']=='skipped_source_covered_by_formal_baseline' and not warm.calls
    assert result['source_runtime']==legacy.PRODUCER and not result['producer_pointers_changed']


def received(originals,tmp_path,monkeypatch):
    network(monkeypatch,originals)
    return legacy.recover(token='explicit software API',runtime_sha=legacy.READER_CONSUMER,
        producer_sha=legacy.READER_PRODUCER,not_after='2026-11-13',out=tmp_path/'received',issuer_head='f'*40)


def admission_args(tmp_path,monkeypatch,*,bad_role=None):
    root=tmp_path/'private';consumer=root/'scoped-consumer';producer=root/'scoped-source-state/runtime'
    consumer.mkdir(parents=True);producer.mkdir(parents=True)
    def git(argv,**_kwargs):
        assert argv[-2:]==['rev-parse','HEAD']
        role='consumer' if str(consumer) in argv else 'producer'
        actual=legacy.READER_CONSUMER if role=='consumer' else legacy.READER_PRODUCER
        return (('0'*40) if role==bad_role else actual)+'\n'
    monkeypatch.setattr(resume.subprocess,'check_output',git)
    return dict(consumer=consumer,producer=producer,state=producer.parent,prepare_out=root/'scoped-prepare',
        delivery_out=root/'scoped-delivery',target=legacy.TARGET,consumer_sha=legacy.READER_CONSUMER,
        producer_sha=legacy.READER_PRODUCER,run=lambda *_a:pytest.fail('Source-only legacy must not execute formal stages'))


def test_new_reader_recovers_and_admits_original_eec_M7_P2_bytes_without_resigning(originals,tmp_path,monkeypatch):
    result=received(originals,tmp_path,monkeypatch)
    path=Path(result['manifest']);raw=path.read_bytes();manifest=json.loads(raw)
    assert (manifest['runtime_sha'],manifest['producer_sha'])==(legacy.CONSUMER,legacy.PRODUCER)
    assert manifest['producer_commit']==legacy.LEGACY_HEAD and manifest['formal_state_archive'] is None
    assert result['validation_reader']['reader_consumer_sha']==legacy.READER_CONSUMER
    args=admission_args(tmp_path,monkeypatch)
    report=resume.admit_resume(path,**args)
    assert report['validation_reader']==result['validation_reader']
    assert report['validation_reader']['source_producer_sha']==legacy.PRODUCER
    assert path.read_bytes()==raw and report['original_json_and_clocks_rewritten'] is False
    assert not report['formal_archive_restored'] and not report['consumer_partial_admitted']
    for name,original in originals['diagnostic'].items():
        assert (path.parent/name).read_bytes()==original
    assert not (args['consumer']/'outputs').exists()
    assert resume.admit_resume(path,**args)['admission_reused'] is True


@pytest.mark.parametrize('bad_role',['consumer','producer'])
def test_legacy_reader_admission_checks_actual_new_Git_roles(originals,tmp_path,monkeypatch,bad_role):
    result=received(originals,tmp_path,monkeypatch)
    args=admission_args(tmp_path,monkeypatch,bad_role=bad_role)
    with pytest.raises(resume.RecoveryError,match='exact_git_role'):resume.admit_resume(result['manifest'],**args)
    assert not args['delivery_out'].exists()


@pytest.mark.parametrize('fault',['source_resigned','formal_archive','reader_mix','arbitrary_reader'])
def test_exact_new_reader_never_relabels_old_source_or_admits_formal_roles(originals,tmp_path,monkeypatch,fault):
    result=received(originals,tmp_path,monkeypatch);path=Path(result['manifest']);value=json.loads(path.read_bytes())
    args=admission_args(tmp_path,monkeypatch)
    if fault=='source_resigned':value['runtime_sha']=legacy.READER_CONSUMER
    elif fault=='formal_archive':value['source_inputs_only']=False
    elif fault=='reader_mix':args['producer_sha']=legacy.PRODUCER
    else:args['consumer_sha']='a'*40
    path.write_bytes(encode(value))
    with pytest.raises((legacy.LegacyRecoveryError,resume.RecoveryError)):
        resume.admit_resume(path,**args)
    assert not args['delivery_out'].exists()


@pytest.mark.parametrize('pair',[(legacy.READER_CONSUMER,legacy.PRODUCER),(legacy.CONSUMER,legacy.READER_PRODUCER),('0'*40,legacy.READER_PRODUCER)])
def test_unknown_or_mixed_receiver_pair_is_a_miss_without_API_or_download(monkeypatch,pair):
    monkeypatch.setattr(legacy,'api_json',lambda *_a:pytest.fail('Unsupported reader must not query source run'))
    assert legacy.discover(token='software',runtime_sha=pair[0],producer_sha=pair[1],not_after=legacy.TARGET) is None
