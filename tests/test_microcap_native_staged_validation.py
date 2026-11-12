"""Real child imports with an explicit small native-protocol fixture."""
from datetime import date
import json
from pathlib import Path
import sys
import types

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import microcap_native_staged_validation as isolation

VERSION = '''from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parent
CACHE_DIR=ROOT/'.microcap_index_cache'
PRICE_DIR=ROOT/'.microcap_index_cache/prices_raw'
SHARE_DIR=ROOT/'.microcap_index_cache/share_change'
SECURITY_MASTER_CACHE=ROOT/'.microcap_index_cache/security_master.csv'
SHARED_PRICE_DIR=SHARED_SHARE_DIR=SHARED_ADJ_PRICE_DIR=None
PRICE_CACHE_DIR=PRICE_DIR
ADJ_PRICE_CACHE_DIR=CACHE_DIR/'prices_qfq'
SHARE_CACHE_DIR=SHARE_DIR
UNIVERSE_CACHE=CACHE_DIR/'active_universe.csv'
CURRENT_ST_CACHE=CACHE_DIR/'current_st.csv'
SHARED_CACHE_DIR=SHARED_PRICE_CACHE_DIR=SHARED_ADJ_PRICE_CACHE_DIR=SHARED_SHARE_CACHE_DIR=None
def load_security_master():return SECURITY_MASTER_CACHE.read_text()
def fetch_share_change():return ROOT
def list_backtest_universe_symbols():
    return sorted(p.stem for p in PRICE_DIR.glob('*.csv') if (SHARE_DIR/p.name).is_file())
freq=SimpleNamespace(**{k:v for k,v in globals().copy().items() if not k.startswith('__')})
base_mod=SimpleNamespace(ROOT=ROOT,CACHE_DIR=CACHE_DIR,freq_mod=freq,fetch_mod=freq)
'''
STATE = '''from pathlib import Path
import microcap_top100_mom16_biweekly_live_v2_0 as v
def validate_state(root,max_anchor_age_days=None,today=None,*,require_current_refresh_proof=True,close_confirmed_session=None):
    root=Path(root).resolve()
    bad=(root/'reject-original-gate').exists()
    return dict(ok=not bad,errors=['original native invariant rejected'] if bad else [],
        symbols=v.base_mod.freq_mod.list_backtest_universe_symbols(),
        master=v.base_mod.freq_mod.load_security_master(),
        strategy_root=str(Path(v.__file__).resolve().parent),
        arguments=dict(max_anchor_age_days=max_anchor_age_days,today=today.isoformat() if today else None,
            require_current_refresh_proof=require_current_refresh_proof,
            close_confirmed_session=close_confirmed_session.isoformat() if close_confirmed_session else None))
'''
DELIVERY = '''from scripts.realtime_state_bundle import validate_state
def inspect_outputs(root,expected,*,new_generation=False):
    report=validate_state(root,require_current_refresh_proof=False)
    return dict(report,expected_date=expected,new_generation=new_generation)
'''

@pytest.fixture
def native_fixture(tmp_path,monkeypatch):
    receiver,stage=tmp_path/'receiver',tmp_path/'staged'
    for root in (receiver,stage):
        (root/'scripts').mkdir(parents=True)
        (root/'microcap_top100_mom16_biweekly_live_v2_0.py').write_text(VERSION,encoding='utf-8')
        (root/'scripts/realtime_state_bundle.py').write_text(STATE,encoding='utf-8')
        (root/'scripts/top100_delivery.py').write_text(DELIVERY,encoding='utf-8')
    for role in ('prices_raw','share_change'):
        folder=stage/'.microcap_index_cache'/role;folder.mkdir(parents=True)
        (folder/'000001.csv').write_text('original bytes\n')
    (stage/'.microcap_index_cache/security_master.csv').write_text('original staged master\n')
    calls=[]
    def original(root,max_anchor_age_days=None,today=None,*,require_current_refresh_proof=True,close_confirmed_session=None):
        calls.append(Path(root))
        assert Path(root)==receiver,'Contaminated parent cannot validate the foreign tree'
        return dict(ok=True,receiver_validation='original unchanged native function')
    state=types.SimpleNamespace(validate_state=original)
    native=types.ModuleType('scripts.top100_cloud_delivery');native.state=state
    def original_inspect(root,expected,*,new_generation=False):
        assert Path(root)==receiver,'Contaminated parent cannot inspect the foreign tree'
        return dict(ok=True,expected_date=expected,receiver_inspection='original unchanged native function')
    native.delivery=types.SimpleNamespace(inspect_outputs=original_inspect)
    native.SOURCE_FILES=['microcap_top100_mom16_biweekly_live_v2_0.py',
        'scripts/realtime_state_bundle.py','scripts/top100_delivery.py']
    def restore(root,bundle,expected):
        inspection=native.delivery.inspect_outputs(stage,expected,new_generation=False)
        if not inspection['ok']:raise ValueError('; '.join(inspection['errors']))
        report=state.validate_state(stage,max_anchor_age_days=None,today=date(2026,11,13),
            require_current_refresh_proof=False,close_confirmed_session=date(2026,11,12))
        if not report['ok']:raise ValueError('; '.join(report['errors']))
        receiver_report=state.validate_state(root,require_current_refresh_proof=False)
        receiver_inspection=native.delivery.inspect_outputs(root,expected)
        return dict(ok=True,expected_date=expected,native_report=report,receiver_report=receiver_report,
            native_inspection=inspection,receiver_inspection=receiver_inspection)
    native.restore=restore
    package=types.ModuleType('scripts');package.__path__=[];package.top100_cloud_delivery=native
    monkeypatch.setitem(sys.modules,'scripts',package)
    monkeypatch.setitem(sys.modules,'scripts.top100_cloud_delivery',native)
    return receiver,stage,native,original,calls

def test_fresh_child_preserves_report_arguments_and_receiver_validation(native_fixture,tmp_path):
    receiver,stage,native,original,calls=native_fixture
    before={str(p.relative_to(stage)):p.read_bytes() for p in stage.rglob('*') if p.is_file()}
    result=isolation.restore_isolated(receiver,tmp_path/'original.zip','2026-11-12',tmp_path/'evidence')
    assert result['native_report']==dict(ok=True,errors=[],symbols=['000001'],master='original staged master\n',
        strategy_root=str(stage),arguments=dict(max_anchor_age_days=None,today='2026-11-13',
            require_current_refresh_proof=False,close_confirmed_session='2026-11-12'))
    assert result['receiver_report']['receiver_validation']=='original unchanged native function'
    assert calls==[receiver] and native.state.validate_state is original
    assert {str(p.relative_to(stage)):p.read_bytes() for p in stage.rglob('*') if p.is_file()}==before
    receipt=json.loads((tmp_path/'evidence/isolation-receipt.json').read_bytes())
    assert [v['native_function'] for v in receipt['original_native_validation_checks']]==['inspect_outputs','validate_state']
    assert result['native_inspection']['strategy_root']==str(stage)
    assert result['native_inspection']['expected_date']=='2026-11-12'
    assert result['native_inspection']['new_generation'] is False
    assert result['receiver_inspection']['receiver_inspection']=='original unchanged native function'
    assert receipt['source_rewritten'] is False and receipt['validation_rules_changed'] is False

def test_false_native_gate_is_not_promoted_and_original_function_is_restored(native_fixture,tmp_path):
    receiver,stage,native,original,calls=native_fixture
    (stage/'reject-original-gate').write_text('original invalid input')
    with pytest.raises(ValueError,match='original native invariant rejected'):
        isolation.restore_isolated(receiver,tmp_path/'original.zip','2026-11-12',tmp_path/'evidence')
    assert not calls and native.state.validate_state is original
    report=json.loads((tmp_path/'evidence/check-1/native-report.json').read_bytes())
    assert report['ok'] is False and report['errors']==['original native invariant rejected']

def test_altered_staged_source_never_launches_native_child(native_fixture,tmp_path):
    receiver,stage,native,original,_=native_fixture
    (stage/native.SOURCE_FILES[0]).write_text(VERSION+'\n# altered source\n',encoding='utf-8')
    with pytest.raises(ValueError,match='source differs'):
        isolation.restore_isolated(receiver,tmp_path/'original.zip','2026-11-12',tmp_path/'evidence')
    assert native.state.validate_state is original and not (tmp_path/'evidence/check-1').exists()

def test_child_exception_retains_native_stderr_and_never_returns_success(native_fixture,tmp_path):
    receiver,stage,native,original,_=native_fixture
    source=STATE.replace('bad=(root/',"raise RuntimeError('original-native-child-error')\n    bad=(root/")
    for root in (receiver,stage):(root/'scripts/realtime_state_bundle.py').write_text(source,encoding='utf-8')
    with pytest.raises(ValueError,match='process failed'):
        isolation.restore_isolated(receiver,tmp_path/'original.zip','2026-11-12',tmp_path/'evidence')
    assert native.state.validate_state is original
    assert 'original-native-child-error' in (tmp_path/'evidence/check-1/stderr.log').read_text()
    assert 'original-native-child-error' in (tmp_path/'native-staged-validation-failure.stderr.log').read_text()
    assert not (tmp_path/'evidence/check-1/native-report.json').exists()
