import copy
import io
import csv
import json
import hashlib
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
from build_microcap_member_digest import render, REVISIONS
from restore_microcap_member_checkpoint import unpack
from check_microcap_member_send import choose
from restore_microcap_member_report import verify as verify_saved
from run_microcap_member_local import validate_local_window, BJ
from datetime import datetime


def result():
    return {'schema':'member_daily_result_v1','market_date':'2026-11-06','publication_mode':'close_confirmed',
            'generated_at':'2026-11-06T16:00:00+08:00','status':'OK',
            'versions':[{'version':v,'revision':r,'status':'verified','signal':{'current_holding':'cash','next_holding':'cash','next_session_target_scale':0,'strategy_revision':r,'date':'2026-11-06','quote_trade_date':'2026-11-06','quote_coverage':'100/100','signal_timing':'close_confirmed','official_close_confirmed_signal':True}}
                        for v,r in REVISIONS.items()], 'warnings':[]}


def test_complete_preserves_cash_zero_and_does_not_claim_maintenance_verified():
    value=result(); before=copy.deepcopy(value)
    meta=render(value,expected_date='2026-11-06')
    assert meta['status']=='OK' and '空仓 → 空仓；目标尺度 0' in meta['body']
    assert '未核验' in meta['body'] and value==before


def test_partial_keeps_other_verified_versions():
    value=result(); value['versions'][0].update(status='partial',signal=None,reason='hedge missing')
    meta=render(value,expected_date='2026-11-06')
    assert meta['status']=='PARTIAL' and 'hedge missing' in meta['body'] and 'v2.5' in meta['body']
    assert meta['versions'][2]['status']=='verified'


def test_all_missing_is_explicit_information_report_and_separate_transaction():
    value=result()
    for item in value['versions']: item.update(status='partial',signal=None,reason='no prices')
    meta=render(value,expected_date='2026-11-06')
    assert '未形成当日完整信号' in meta['subject']
    assert choose(meta,'normal')=='normal-partial-information'
    assert choose(render(result(),expected_date='2026-11-06'),'normal')=='normal'


def test_old_basket_reference_is_not_verified_target():
    value=result(); value['versions'][0]['status']='reference'
    meta=render(value,expected_date='2026-11-06')
    assert meta['status']=='PARTIAL' and '旧篮子观察' in meta['body'] and '下一正式目标未确认' in meta['body']


@pytest.mark.parametrize('mutation',[
    lambda x:x.update(market_date='2026-11-05'),
    lambda x:x['versions'][0].update(revision='retired'),
    lambda x:x['versions'][0].update(signal=None),
    lambda x:x['versions'].append(x['versions'][0]),
    lambda x:x['versions'][0]['signal'].update(quote_trade_date='2026-11-05'),
    lambda x:x['versions'][0]['signal'].update(quote_coverage='99/100'),
])
def test_identity_and_verified_shape_cannot_be_filled_from_stdout(mutation):
    value=result(); mutation(value)
    with pytest.raises(ValueError):render(value,expected_date='2026-11-06')


@pytest.mark.parametrize('bad',['../checkpoint.json','/checkpoint.json','..\\checkpoint.json','C:/checkpoint.json'])
def test_small_restore_rejects_escape(tmp_path,bad):
    raw=io.BytesIO()
    with zipfile.ZipFile(raw,'w') as z:
        z.writestr('checkpoint.json','{}');z.writestr(bad,'bad')
    with pytest.raises(ValueError):unpack(raw.getvalue(),tmp_path/'target')
    assert not (tmp_path/'target').exists()


def test_small_restore_requires_fresh_target(tmp_path):
    raw=io.BytesIO()
    with zipfile.ZipFile(raw,'w') as z:z.writestr('checkpoint.json','{}')
    target=tmp_path/'target';unpack(raw.getvalue(),target)
    with pytest.raises(FileExistsError):unpack(raw.getvalue(),target)


def test_daily_workflow_has_no_maintenance_or_whole_restore_dependency():
    root=Path(__file__).parents[1]
    flow=(root/'.github/workflows/microcap-member-daily.yml').read_text()
    assert 'delivery-regression.yml' not in flow and 'refresh-all' not in flow and 'whole_state_recovery' not in flow
    assert flow.index('Publish verified member checkpoint') < flow.index('Send Gmail')
    assert 'check_microcap_delivery.py' in flow and 'check_microcap_member_send.py' in flow
    assert 'group: microcap-realtime-digest' in flow
    maintenance=(root/'.github/workflows/microcap-member-maintenance.yml').read_text()
    assert 'member_period_handoff.py schedule' in maintenance and 'scripts/top100_delivery.py refresh-all' in maintenance
    assert 'MAIL_PASSWORD' not in maintenance


def saved_report(folder):
    value=result()
    for item in value['versions']:
        signal=item['signal'];signal.update(current_execution_scale=0,trade_state='hold')
        with (folder/(item['version'].replace('.','_')+'_signal.csv')).open('w',newline='',encoding='utf8') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(signal));writer.writeheader();writer.writerow(signal)
    (folder/'result.json').write_text(json.dumps(value),encoding='utf8')
    for name in ('checkpoint.json','quotes.json','hedge.json'):(folder/name).write_text('{}',encoding='utf8')
    bind_manifest(folder)


def bind_manifest(folder):
    files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir() if p.name!='manifest.json'}
    (folder/'manifest.json').write_text(json.dumps({'schema':'member_daily_saved_report_v1','strategy_sha':'a'*40,'market_date':'2026-11-06','files':files}),encoding='utf8')


def test_reviewed_report_checks_unchanged_closure_and_csv_result_parity(tmp_path):
    saved_report(tmp_path)
    verify_saved(tmp_path,'a'*40,'2026-11-06')
    p=tmp_path/'v2_0_signal.csv';p.write_text(p.read_text().replace('cash,cash','cash,long_microcap'),encoding='utf8')
    bind_manifest(tmp_path)
    with pytest.raises(ValueError,match='CSV and result conflict'):verify_saved(tmp_path,'a'*40,'2026-11-06')


def test_reviewed_report_rejects_unlisted_or_wrong_commit(tmp_path):
    saved_report(tmp_path)
    with pytest.raises(ValueError,match='source/date'):verify_saved(tmp_path,'b'*40,'2026-11-06')
    (tmp_path/'unlisted.json').write_text('{}')
    with pytest.raises(ValueError,match='closure'):verify_saved(tmp_path,'a'*40,'2026-11-06')


def test_local_preparation_and_intraday_cutoff_are_separate():
    validate_local_window('2026-11-06',True,datetime(2026,11,6,10,0,tzinfo=BJ))
    validate_local_window('2026-11-06',False,datetime(2026,11,6,14,59,59,tzinfo=BJ))
    with pytest.raises(ValueError):validate_local_window('2026-11-06',False,datetime(2026,11,6,15,0,tzinfo=BJ))
    with pytest.raises(ValueError):validate_local_window('2026-11-05',True,datetime(2026,11,6,10,0,tzinfo=BJ))
