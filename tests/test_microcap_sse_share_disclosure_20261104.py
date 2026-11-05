"""The actual native exporter output must survive the normal report path."""
import copy
import importlib.util
import json
from pathlib import Path
import pytest
import yaml

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('sse_disclosure_digest',ROOT/'scripts/build_microcap_realtime_digest.py')
digest=importlib.util.module_from_spec(spec);spec.loader.exec_module(digest)
FIXTURE=ROOT/'tests/fixtures/microcap/sse_share_disclosure_20261104.json'


def source(tmp_path,change=None):
    value=json.loads(FIXTURE.read_bytes())
    if change:change(value)
    path=tmp_path/'native-source-disclosure.json';path.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
    return path


def test_actual_native_disclosure_is_prominent_and_does_not_claim_no_data_issue(tmp_path):
    from test_microcap_digest_email_body import MicrocapDigestEmailBodyTests
    helper=MicrocapDigestEmailBodyTests();items=[]
    for version in ('v2.0','v2.3','v2.5'):
        items.append({'version':version,'status':'OK','status_note':'','fields':{
            **helper.identity_fields(version),'date':'2026-11-04','current_holding':'cash','next_holding':'cash',
            'current_execution_scale':'0','next_session_actionable_scale':'0'}})
    disclosures=digest.load_share_source_disclosures(str(source(tmp_path)),'2026-11-04')
    subject,body=digest.build_compact_digest(items,date_s='2026-11-04',run_url='',strategy_sha='formal',
        publication_mode='close_confirmed',share_source_disclosures=disclosures)
    assert '[异常]' not in subject and '## 股本来源分歧' in body
    assert '28514.52万股' in body and '28483.52万股' in body and '不据此宣称注销已完成' in body
    assert '\n风险/数据异常：无' not in body
    assert len([line for line in body.splitlines() if line.startswith('| v2.')])==3


@pytest.mark.parametrize('change',[
    lambda d:d.update(native_verified=False),
    lambda d:d.update(signal_date='2026-11-03'),
    lambda d:d['disclosures'].append(copy.deepcopy(d['disclosures'][0])),
    lambda d:d['disclosures'][0].update(symbol='603029'),
    lambda d:d['disclosures'][0].update(review_sha256='0'*64),
    lambda d:d['disclosures'][0].update(text='未披露分歧，注销已完成'),
    lambda d:d['disclosures'][0]['selection'].update(total_shares_10k='28483.52'),
    lambda d:d['disclosures'][0]['selection'].update(official_data_date='2026-11-03'),
    lambda d:d['disclosures'][0]['selection'].update(source_conflict_disclosed=False),
    lambda d:d['disclosures'][0]['selection'].update(legal_cancellation_completion_not_claimed=False),
    lambda d:d['disclosures'][0].update(source_files={}),
])
def test_bad_or_unverified_source_text_cannot_enter_normal_body(tmp_path,change):
    with pytest.raises(ValueError):digest.load_share_source_disclosures(str(source(tmp_path,change)),'2026-11-04')


def test_source_date_cannot_be_relabelled_for_later_signal(tmp_path):
    with pytest.raises(ValueError):digest.load_share_source_disclosures(str(source(tmp_path)),'2026-11-05')


def test_normal_workflow_requires_native_success_before_build_and_original_smtp_gates():
    workflow=yaml.load((ROOT/'.github/workflows/microcap-realtime-digest.yml').read_text(),Loader=yaml.BaseLoader)
    steps=workflow['jobs']['send']['steps'];byname={s.get('name'):s for s in steps};names=[s.get('name') for s in steps]
    exporter=byname['Verify current share-source disclosures'];build=byname['Build Markdown digest']
    assert "steps.whole_delivery.outputs.validated == 'true'" in exporter['if']
    assert names.index('Verify current share-source disclosures')<names.index('Build Markdown digest')<names.index('Persist normal-send intent before SMTP')<names.index('Send Gmail')
    assert 'steps.share_source_disclosures.outcome' in build['run'] and '|| exit 1' in build['run']
    assert '--share-source-disclosures artifacts/share-source-disclosures.json' in build['run']
    assert "steps.digest.outputs.status == 'OK'" in byname['Persist normal-send intent before SMTP']['if']


def late_disclosure_value():
    text='301288：11/06补充核验11/05公告事件，总股本108010000股不变；不声称11/05收盘前已取得该公告。'
    review='b716606578531dbb67ed5f1ea20d91f25330cac284e5f96818d4ae65bfa4cfc9'
    selection={'disclosure':text,'review_sha256':review,'package_sha256':'3af53c7fea86b3334321112981747560e9fcea9dba5ee7667c9f011d8a70f4ff',
        'signal_day':'2026-11-05','supplemental_verification_day':'2026-11-06'}
    value = {'schema':'verified_share_source_disclosures_v1','signal_date':'2026-11-05','native_verified':True,
        'disclosures':[{'symbol':'301288','signal_date':'2026-11-05','review_sha256':review,'text':text,
        'source_files':{'301288.json':'test-structural-evidence'},'selection':selection}]}
    text='603028：按精确批准新增上交所11/05数据日股本284835200股，较11/04批准观测减少310000股；保留11/04及以前记录。东方财富/新浪事件日期为11/04，本次不据此追改旧观测或断言法律生效日。11/06补充核验。'
    review='fcdbad5e9ae9f1fd63b03dac362105f3d0d5031385dfab578942f9c85962a6eb'
    selection={'kind':'sse_dated_transition_20261105_v1','review_sha256':review,
        'package_sha256':'7fe387b7a1fbcd8548fff0533852c6421d7b3c59502cee361d0da86734b18dad',
        'official_data_date':'2026-11-05','signal_day':'2026-11-05','total_shares_10k':'28483.52',
        'previous_total_shares_10k':'28514.52','provider_event_date':'2026-11-04',
        'supplemental_verification_day':'2026-11-06','historical_prefix_preserved':True,
        'legal_effective_date_not_claimed':True,'disclosure':text}
    value['disclosures'].append({'symbol':'603028','signal_date':'2026-11-05','review_sha256':review,
        'text':text,'source_files':{'603028.json':'synthetic-structural-test'},'selection':selection})
    return value



@pytest.mark.parametrize('fault',[None,'date','text','package','source','missing','unverified'])
def test_301288_supplement_cannot_lose_late_verification(tmp_path,fault):
    value=late_disclosure_value();item=value['disclosures'][0]
    if fault=='date':item['selection']['supplemental_verification_day']='2026-11-05'
    elif fault=='text':item['text']='same-day verified'
    elif fault=='package':item['selection']['package_sha256']='0'*64
    elif fault=='source':item['source_files']={}
    elif fault=='missing':value['disclosures']=[]
    elif fault=='unverified':value['native_verified']=False
    path=tmp_path/'source.json';path.write_text(json.dumps(value),encoding='utf8')
    if fault:
        with pytest.raises(ValueError):digest.load_share_source_disclosures(str(path),'2026-11-05')
    else:assert digest.load_share_source_disclosures(str(path),'2026-11-05')==value['disclosures']


@pytest.mark.parametrize('fault',['remove603028','duplicate','history','date','count','package','legal'])
def test_both_d5_disclosures_and_precise_transition_required(tmp_path,fault):
    value=late_disclosure_value();item=value['disclosures'][1]
    if fault=='remove603028':value['disclosures'].pop()
    elif fault=='duplicate':value['disclosures'][1]=value['disclosures'][0]
    elif fault=='history':item['selection']['historical_prefix_preserved']=False
    elif fault=='date':item['selection']['official_data_date']='2026-11-04'
    elif fault=='count':item['selection']['total_shares_10k']='28514.52'
    elif fault=='package':item['selection']['package_sha256']='0'*64
    else:item['selection']['legal_effective_date_not_claimed']=False
    path=tmp_path/'source.json';path.write_text(json.dumps(value),encoding='utf-8')
    with pytest.raises(ValueError):digest.load_share_source_disclosures(str(path),'2026-11-05')
