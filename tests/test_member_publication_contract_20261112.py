"""C3/C4/C5: original D11 bytes plus explicitly synthetic publication faults.

The frozen report is source evidence, not a request to refresh data or trade.
Noncash R/R+1 cases below are software fixtures, never actual future signals.
"""
import copy
import csv
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import socket
import subprocess
import sys
import urllib.request
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import build_microcap_member_digest as digest
from check_microcap_member_send import choose

FIXTURE=ROOT/'tests/fixtures/member_daily_20261111'


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args,**kwargs):raise AssertionError('Publication tests forbid network/SMTP')
    monkeypatch.setattr(socket,'create_connection',blocked)
    monkeypatch.setattr(socket.socket,'connect',blocked)
    monkeypatch.setattr(socket.socket,'connect_ex',blocked)
    monkeypatch.setattr(urllib.request,'urlopen',blocked)


@pytest.fixture
def original(tmp_path):
    proof=json.loads((FIXTURE/'provenance.json').read_bytes())
    raw=(FIXTURE/'daily-result.zip').read_bytes()
    assert hashlib.sha256(raw).hexdigest()==proof['archive_sha256']
    directory=tmp_path/'daily-result';directory.mkdir()
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        assert set(archive.namelist())==set(proof['files'])
        for name,expected in proof['files'].items():
            value=archive.read(name);assert hashlib.sha256(value).hexdigest()==expected
            assert Path(name).name==name
            (directory/name).write_bytes(value)
    return directory


def read_result(directory):return json.loads((directory/'result.json').read_bytes())


def write_result(directory,result):
    (directory/'result.json').write_text(json.dumps(result,ensure_ascii=False,allow_nan=False),encoding='utf8')


def sync_signals(directory,result):
    write_result(directory,result)
    for item in result['versions']:
        signal=item.get('signal');path=directory/(item['version'].replace('.','_')+'_signal.csv')
        if signal is None:
            path.unlink(missing_ok=True)
        else:
            with path.open('w',encoding='utf8',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=list(signal));writer.writeheader();writer.writerow(signal)


def run_main(monkeypatch,directory,out,*,clock='2026-11-11T16:02:00+08:00',module=digest,checkpoint=None):
    if module is digest:monkeypatch.setattr(digest,'now_bj',lambda:datetime.fromisoformat(clock))
    argv=['render','--result',str(directory/'result.json'),'--context-root',str(directory),
        '--expected-date',read_result(directory)['market_date'],'--out',str(out)]
    if checkpoint is not None:argv+=['--context-checkpoint',str(checkpoint)]
    monkeypatch.setattr(sys,'argv',argv)
    assert module.main()==0
    return json.loads((out/'metadata.json').read_bytes())


def test_real_d11_complete_field_csv_binding_preserves_original_json_csv_and_body(original,tmp_path,monkeypatch):
    before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in original.iterdir()}
    proof=json.loads((FIXTURE/'provenance.json').read_bytes())['sent_reference']
    raw=(FIXTURE/proof['file']).read_bytes().replace(b'\r\n',b'\n')
    assert hashlib.sha256(raw).hexdigest()==proof['sha256']
    old_meta=json.loads(raw)
    assert hashlib.sha256(old_meta['body'].encode()).hexdigest()==proof['body_sha256']
    new_meta=run_main(monkeypatch,original,tmp_path/'after')
    assert new_meta['status']=='OK'
    assert new_meta['body']==old_meta['body'] and new_meta['subject']==old_meta['subject']
    assert new_meta['member_actions']=={}
    assert {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in original.iterdir()}==before
    with zipfile.ZipFile(new_meta['attachment']) as archive:
        for version,digest_value in new_meta['final_signal_sha256'].items():
            name=version.replace('.','_')+'_signal.csv'
            assert archive.read(name)==(original/name).read_bytes()
            assert hashlib.sha256(archive.read(name)).hexdigest()==digest_value
        assert archive.read('result.json')==(original/'result.json').read_bytes()
    print(json.dumps({'D11_csv_and_json_unchanged':True,'D11_body_unchanged':True,
        'signal_sha256':new_meta['final_signal_sha256'],'body_sha256':hashlib.sha256(new_meta['body'].encode()).hexdigest()}))


@pytest.mark.parametrize('fault',['missing','zero_rows','two_rows','duplicate_header','missing_field','extra_field',
    'bad_utf8','unterminated_quote','holding','optional_metric','official','tiny_scale','json_only','status'])
def test_corrupt_missing_or_any_field_conflicting_csv_never_writes_normal_metadata(original,tmp_path,monkeypatch,fault):
    path=original/'v2_0_signal.csv';raw=path.read_text(encoding='utf-8-sig');rows=list(csv.DictReader(io.StringIO(raw)))
    header=list(rows[0]);result=read_result(original)
    if fault=='missing':path.unlink()
    elif fault=='zero_rows':path.write_text(raw.splitlines()[0]+'\n',encoding='utf8')
    elif fault=='two_rows':path.write_text(raw+raw.splitlines()[1]+'\n',encoding='utf8')
    elif fault=='duplicate_header':path.write_text(raw.splitlines()[0]+','+header[0]+'\n'+raw.splitlines()[1]+',cash\n',encoding='utf8')
    elif fault=='bad_utf8':path.write_bytes(b'\xff\xfe')
    elif fault=='unterminated_quote':path.write_text('date\n"broken',encoding='utf8')
    elif fault in {'json_only','status'}:
        if fault=='status':result['status']='PARTIAL'
        else:result['versions'][0]['signal']['overheat_window']=123
        write_result(original,result)
    else:
        if fault=='missing_field':rows[0].pop('overheat_window');header.remove('overheat_window')
        elif fault=='extra_field':header.append('unbound_value');rows[0]['unbound_value']='unexpected'
        elif fault=='holding':rows[0]['next_holding']='long_microcap_short_zz1000'
        elif fault=='optional_metric':rows[0]['microcap_mom']='0.12345'
        elif fault=='official':rows[0]['official_close_confirmed_signal']='False'
        elif fault=='tiny_scale':rows[0]['current_execution_scale']='0.00000000001'
        with path.open('w',encoding='utf8',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=header);writer.writeheader();writer.writerows(rows)
    with pytest.raises(ValueError):run_main(monkeypatch,original,tmp_path/'blocked')
    assert not (tmp_path/'blocked/metadata.json').exists()


def test_partial_and_old_basket_reference_keep_independent_information_transactions(original,tmp_path,monkeypatch):
    result=read_result(original);result['status']='PARTIAL'
    result['versions'][0].update(status='partial',signal=None,reason='isolated hedge failure')
    write_result(original,result);(original/'v2_0_signal.csv').unlink()
    meta=run_main(monkeypatch,original,tmp_path/'partial')
    assert meta['status']=='PARTIAL' and choose(meta,'normal')=='normal-partial-information'
    assert set(meta['final_signal_sha256'])=={'v2.3','v2.5'}


@pytest.mark.parametrize('field,value',[('current_holding','unsupported'),('current_execution_scale',1),
    ('next_session_target_scale',-1),('next_session_target_scale',None)])
def test_even_matching_csv_and_json_cannot_promote_invalid_execution_identity(original,tmp_path,monkeypatch,field,value):
    result=read_result(original);result['versions'][0]['signal'][field]=value;sync_signals(original,result)
    with pytest.raises(ValueError):run_main(monkeypatch,original,tmp_path/'blocked')
    assert not (tmp_path/'blocked/metadata.json').exists()


@pytest.mark.parametrize('field',['quote_trade_date','quote_coverage'])
def test_new_daily_cannot_borrow_saved_checkpoint_exception_for_missing_quotes(original,tmp_path,monkeypatch,field):
    result=read_result(original)
    for item in result['versions']:item['signal'].pop(field)
    sync_signals(original,result)
    with pytest.raises(ValueError):run_main(monkeypatch,original,tmp_path/'blocked')
    assert not (tmp_path/'blocked/metadata.json').exists()


def test_checkpoint_discovery_import_stays_independent_of_pandas_installation():
    source="""import importlib.abc,sys
class DenyPandas(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname=='pandas' or fullname.startswith('pandas.'):
   raise AssertionError('checkpoint discovery must not import pandas')
sys.meta_path.insert(0,DenyPandas())
sys.path.insert(0,sys.argv[1])
import restore_microcap_member_checkpoint
"""
    result=subprocess.run([sys.executable,'-B','-c',source,str(ROOT/'scripts')],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


@pytest.mark.parametrize('mode,timing,official,valid',[
    ('close_confirmed','close_confirmed',True,True),
    ('close_confirmed','close_confirmed_anchor','True',True),
    ('close_confirmed','intraday_hypothetical_if_now_close',False,False),
    ('close_confirmed','close_confirmed',False,False),
    ('close_confirmed','close_confirmed',1,False),
    ('intraday','intraday_hypothetical_if_now_close',False,True),
    ('intraday','close_confirmed',True,False),
    ('intraday','intraday_hypothetical_if_now_close',True,False),
])
def test_each_final_signal_mode_timing_and_official_flag_are_independent_gates(original,tmp_path,monkeypatch,mode,timing,official,valid):
    result=read_result(original);result['publication_mode']=mode
    result['generated_at']='2026-11-11T14:30:00+08:00' if mode=='intraday' else '2026-11-11T16:00:00+08:00'
    for item in result['versions']:item['signal'].update(signal_timing=timing,official_close_confirmed_signal=official)
    sync_signals(original,result)
    clock='2026-11-11T14:31:00+08:00' if mode=='intraday' else '2026-11-11T16:02:00+08:00'
    if valid:assert run_main(monkeypatch,original,tmp_path/'report',clock=clock)['status']=='OK'
    else:
        with pytest.raises(ValueError):run_main(monkeypatch,original,tmp_path/'report',clock=clock)


@pytest.mark.parametrize('clock,generated,mode,valid',[
    ('2026-11-11T15:00:00+08:00','2026-11-11T14:59:59+08:00','intraday',False),
    ('2026-11-11T15:01:00+08:00','2026-11-11T15:00:00+08:00','intraday',False),
    ('2026-11-11T14:59:59+08:00','2026-11-11T14:59:00+08:00','close_confirmed',False),
    ('2026-11-12T00:02:00+08:00','2026-11-12T00:01:00+08:00','close_confirmed',True),
])
def test_publication_cutoff_and_true_cross_midnight_market_date(original,tmp_path,monkeypatch,clock,generated,mode,valid):
    result=read_result(original);result.update(publication_mode=mode,generated_at=generated)
    if mode=='intraday':
        for item in result['versions']:item['signal'].update(signal_timing='intraday_hypothetical_if_now_close',official_close_confirmed_signal=False)
    sync_signals(original,result)
    if valid:assert run_main(monkeypatch,original,tmp_path/'report',clock=clock)['signal_date']=='2026-11-11'
    else:
        with pytest.raises(ValueError):run_main(monkeypatch,original,tmp_path/'report',clock=clock)


def synthetic_rotation(directory,*,explicit):
    """Synthetic current R=12 / next session=13; never actual future evidence."""
    result=read_result(directory);result.update(market_date='2026-11-12',generated_at='2026-11-12T16:00:00+08:00')
    for item in result['versions']:
        active='long_microcap_top100' if item['version']=='v2.5' else 'long_microcap_short_zz1000'
        item['signal'].update(date='2026-11-12',quote_trade_date='2026-11-12',current_holding=active,next_holding=active,
            current_execution_scale=1,next_session_target_scale=1,next_session_actionable_scale=1,trade_state='hold')
        if explicit:item['signal'].update(member_rebalance_required=True,member_rebalance_official=True,
            member_rebalance_actionable=True,member_rebalance_signal_date='2026-11-12',member_rebalance_execution_date='2026-11-13',
            member_enter_count=4,member_exit_count=4)
    cp=json.loads((directory/'checkpoint.json').read_bytes());cp.update(as_of_date='2026-11-12',next_trade_date='2026-11-13')
    period=json.loads((directory/'period.json').read_bytes());period.update(anchor_day='2026-11-12',effective_from='2026-11-13')
    event=dict(period['rebalance_events'][0]);event.update(rebalance_date='2026-11-12',constraint_trade_date='2026-11-12',
        execution_date='2026-11-12',effective_date='2026-11-12',return_start_date='2026-11-13',entry_count='4',exit_count='4')
    period['rebalance_events']=[event];(directory/'period.json').write_text(json.dumps(period),encoding='utf8')
    with (directory/'turnover.csv').open('w',encoding='utf8',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(event));writer.writeheader();writer.writerow(event)
    for key in ('period','turnover'):cp[key]['sha256']=hashlib.sha256((directory/cp[key]['path']).read_bytes()).hexdigest()
    result['period_sha256']=cp['period']['sha256']
    (directory/'checkpoint.json').write_text(json.dumps(cp),encoding='utf8');sync_signals(directory,result)
    return result


@pytest.mark.parametrize('explicit',[False,True])
def test_same_non_cash_holding_with_current_dated_member_change_is_not_continue_holding(original,tmp_path,monkeypatch,explicit):
    synthetic_rotation(original,explicit=explicit)
    meta=run_main(monkeypatch,original,tmp_path/'rotation',clock='2026-11-12T16:01:00+08:00')
    assert set(meta['member_actions'])==set(digest.REVISIONS)
    assert '继续持有' not in meta['body']
    for action in meta['member_actions'].values():
        assert action['official'] and action['actionable'] and action['required']
        assert action['entry_count']==action['exit_count']==4
        assert action['signal_date']=='2026-11-12' and action['execution_date']=='2026-11-13'
        assert action['source']==('final_csv' if explicit else 'bound_period_turnover')
    assert '调入 4，调出 4；信号日 2026-11-12，执行日 2026-11-13' in meta['body']


@pytest.mark.parametrize('fault',['cash','entry','exit','history','preview','unofficial','not_required','wrong_next',
    'missing_next','zero_counts','partial_flags','turnover_conflict','turnover_hash','period_hash','wrong_period',
    'explicit_turnover_hash','explicit_missing_turnover','explicit_old_period'])
def test_cash_history_preview_or_unbound_member_events_never_become_current_rotation(original,tmp_path,monkeypatch,fault):
    result=synthetic_rotation(original,explicit=fault not in {'turnover_conflict','turnover_hash','period_hash'})
    cp=json.loads((original/'checkpoint.json').read_bytes())
    for item in result['versions']:
        signal=item['signal']
        if fault=='cash':signal.update(current_holding='cash',next_holding='cash',current_execution_scale=0,next_session_target_scale=0,next_session_actionable_scale=0)
        elif fault=='entry':signal.update(current_holding='cash',current_execution_scale=0)
        elif fault=='exit':signal.update(next_holding='cash',next_session_target_scale=0,next_session_actionable_scale=0)
        elif fault=='history':signal['member_rebalance_signal_date']='2026-10-29'
        elif fault=='preview':signal['member_rebalance_actionable']=False
        elif fault=='unofficial':signal['member_rebalance_official']=False
        elif fault=='not_required':signal['member_rebalance_required']=False
        elif fault=='zero_counts':signal.update(member_enter_count=0,member_exit_count=0)
        elif fault=='partial_flags':signal.pop('member_rebalance_official')
    if fault=='wrong_next':cp['next_trade_date']='2026-11-16'
    elif fault=='missing_next':cp.pop('next_trade_date')
    elif fault in {'turnover_conflict','turnover_hash','explicit_turnover_hash'}:
        p=original/'turnover.csv';p.write_text(p.read_text().replace(',4,4,',',3,4,'),encoding='utf8')
        if fault=='turnover_conflict':cp['turnover']['sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
    elif fault=='period_hash':cp['period']['sha256']='0'*64
    elif fault=='wrong_period':result['period_sha256']='0'*64
    elif fault=='explicit_missing_turnover':(original/'turnover.csv').unlink()
    elif fault=='explicit_old_period':
        p=original/'period.json';period=json.loads(p.read_bytes());period['anchor_day']='2026-10-29'
        p.write_text(json.dumps(period),encoding='utf8');cp['period']['sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
        result['period_sha256']=cp['period']['sha256']
    (original/'checkpoint.json').write_text(json.dumps(cp),encoding='utf8');sync_signals(original,result)
    uncertain={'wrong_next','missing_next','zero_counts','partial_flags','turnover_conflict','turnover_hash',
        'wrong_period','explicit_turnover_hash','explicit_missing_turnover','explicit_old_period'}
    if fault in uncertain:
        with pytest.raises(ValueError,match='rotation'):run_main(monkeypatch,original,tmp_path/'background',clock='2026-11-12T16:01:00+08:00')
        assert not (tmp_path/'background/metadata.json').exists()
        return
    meta=run_main(monkeypatch,original,tmp_path/'background',clock='2026-11-12T16:01:00+08:00')
    assert meta['member_actions']=={} and '名单调仓（调入' not in meta['body']
    if fault=='cash':assert '维持空仓，无需操作' in meta['body']
    elif fault=='entry':assert '下一执行日模型入场' in meta['body']
    elif fault=='exit':assert '下一执行日模型退出' in meta['body']
