"""Freeze one member session across midnight; all SMTP below is isolated."""
from datetime import date, datetime
import hashlib
import io
import json
from pathlib import Path
import socket
import sys
import zipfile

import pytest
import yaml

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import check_microcap_delivery as gate
import check_microcap_member_send as transaction
import send_report
from test_member_publication_contract_20261112 import original, run_main
from test_microcap_smtp_receipt import smtp_fixture
from test_microcap_midnight_delivery_recovery import trusted_receipt
import restore_ic_im_v1_3_ledger as helper

DAY='2026-11-11'
NEXT_DAY='2026-11-12'
MARKER=gate.delivery_marker_name(date.fromisoformat(DAY),'close_confirmed')
AFTER_MIDNIGHT=datetime.fromisoformat(NEXT_DAY+'T00:00:01+08:00')


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('Midnight tests prohibit real network/SMTP')
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    monkeypatch.setattr(socket,'create_connection',forbidden)


def run_gate(monkeypatch,names,*,frozen=True,extra=()):
    outputs=[];queries=[]
    def fetch(repo,token,name,api):
        queries.append(name)
        return {'artifacts':[dict(id=1,name=name,expired=False)] if name in names else []}
    monkeypatch.setattr(gate,'now_utc',lambda:AFTER_MIDNIGHT)
    monkeypatch.setattr(gate,'fetch_artifacts',fetch)
    monkeypatch.setattr(gate,'write_outputs',outputs.append)
    argv=['gate','--publication-mode','close_confirmed','--repository','software/isolated','--token','software']
    if frozen:argv+=['--delivery-date',DAY]
    monkeypatch.setattr(sys,'argv',argv+list(extra))
    gate.main()
    return outputs[0],queries


def metadata_file(tmp_path,meta):
    path=tmp_path/'metadata.json'
    path.write_text(json.dumps(meta,ensure_ascii=False),encoding='utf8')
    return path


def test_exact_workflow_carries_frozen_session_to_gate_and_transaction_to_final_send():
    steps=yaml.safe_load((ROOT/'.github/workflows/microcap-member-daily.yml').read_bytes())['jobs']['send']['steps']
    by_name={s.get('name'):s for s in steps}
    assert "--delivery-date '${{ steps.session.outputs.date }}'" in by_name['Check delivery marker']['run']
    assert "--expected-date '${{ steps.session.outputs.date }}'" in by_name['Build member digest']['run']
    assert "--normal-marker '${{ steps.gate.outputs.marker_name }}'" in by_name['Choose independent complete or partial send transaction']['run']
    assert "--member-delivery-marker '${{ steps.transaction.outputs.marker_name }}'" in by_name['Send Gmail']['run']
    for name,suffix in [('Persist normal-send intent before SMTP','-send-intent'),
                        ('Preserve accepted SMTP receipt','-smtp-accepted'),('Mark digest delivered','')]:
        assert by_name[name]['with']['name']=='${{ steps.transaction.outputs.marker_name }}'+suffix


def test_frozen_delivered_D11_remains_D11_after_midnight_and_never_rekeys(monkeypatch):
    result,queries=run_gate(monkeypatch,{MARKER})
    assert result['delivery_date']==DAY and result['marker_name']==MARKER
    assert result['should_send']=='false' and result['recover_marker']=='false'
    assert queries==[MARKER]


def test_unbound_legacy_gate_cannot_attach_D11_report_to_D12_transaction(original,tmp_path,monkeypatch):
    meta=run_main(monkeypatch,original,tmp_path/'report',clock=NEXT_DAY+'T00:00:03+08:00')
    result,_=run_gate(monkeypatch,{MARKER},frozen=False)
    assert result['delivery_date']==NEXT_DAY and result['should_send']=='true'
    with pytest.raises(ValueError,match='transaction date/mode'):
        transaction.choose(meta,result['marker_name'])


@pytest.mark.parametrize('status',['OK','PARTIAL'])
def test_full_D11_renderer_transaction_and_final_smtp_keep_original_day_after_midnight(original,tmp_path,monkeypatch,smtp_fixture,status):
    # Original D11 result/CSV are unchanged. PARTIAL is an explicit software-only mutation.
    if status=='PARTIAL':
        p=original/'result.json';value=json.loads(p.read_bytes());value['status']='PARTIAL'
        value['versions'][0].update(status='partial',signal=None,reason='isolated transport failure')
        p.write_text(json.dumps(value),encoding='utf8');(original/'v2_0_signal.csv').unlink()
    meta=run_main(monkeypatch,original,tmp_path/'rendered',clock=NEXT_DAY+'T00:00:03+08:00')
    gate_result,_=run_gate(monkeypatch,set())
    marker=transaction.choose(meta,gate_result['marker_name'])
    assert gate_result['should_send']=='true' and marker==MARKER+('-partial-information' if status=='PARTIAL' else '')
    meta_path=tmp_path/'rendered/metadata.json';before=meta_path.read_bytes()
    receipt=tmp_path/'receipt/smtp-accepted.json'
    monkeypatch.setattr(sys,'argv',['send',str(meta_path),'--receipt',str(receipt),'--member-delivery-marker',marker])
    assert send_report.main()==0
    smtp,_=smtp_fixture
    assert smtp.send_message.call_count==1
    accepted=json.loads(receipt.read_bytes())
    assert accepted['member_delivery_marker']==marker
    assert accepted['metadata_sha256']==hashlib.sha256(before).hexdigest()
    assert meta_path.read_bytes()==before and meta['signal_date']==DAY


@pytest.mark.parametrize('names',[{MARKER+'-send-intent'}, {MARKER+'-send-intent',MARKER+'-smtp-accepted'}])
def test_frozen_original_transaction_retains_unknown_guard_and_receipt_only_recovery(monkeypatch,names):
    verified=[];monkeypatch.setattr(gate,'verify_smtp_receipt',lambda *a:verified.append(a))
    if MARKER+'-smtp-accepted' not in names:
        with pytest.raises(RuntimeError,match='prior SMTP send intent'):run_gate(monkeypatch,names)
        assert not verified
    else:
        result,_=run_gate(monkeypatch,names)
        assert result['marker_name']==MARKER and result['delivery_date']==DAY
        assert result['recover_marker']=='true' and result['should_send']=='false'
        assert verified[0][4:]==(date.fromisoformat(DAY),'close_confirmed','')


@pytest.mark.parametrize('status,marker',[
    ('OK',''),('OK',MARKER.replace(DAY,NEXT_DAY)),
    ('OK',MARKER.replace('close_confirmed','realtime')),('OK',MARKER+'-partial-information'),
    ('PARTIAL',MARKER),('PARTIAL',MARKER.replace(DAY,NEXT_DAY)+'-partial-information'),
])
def test_wrong_missing_or_cross_status_final_transaction_fails_before_smtp_or_receipt(tmp_path,monkeypatch,smtp_fixture,status,marker):
    meta=dict(schema='member_daily_report_v1',status=status,signal_date=DAY,publication_mode='close_confirmed',subject='software',body='software')
    path=metadata_file(tmp_path,meta);receipt=tmp_path/'uncreated/smtp.json'
    monkeypatch.setattr(sys,'argv',['send',str(path),'--receipt',str(receipt),'--member-delivery-marker',marker])
    with pytest.raises(ValueError):send_report.main()
    smtp,_=smtp_fixture;assert smtp.send_message.call_count==0
    assert not receipt.parent.exists()


@pytest.mark.parametrize('day,mode',[('20261111','close_confirmed'),('2026-11-13','close_confirmed'),(DAY,'realtime')])
def test_invalid_frozen_session_date_or_intraday_rollover_is_rejected(monkeypatch,day,mode):
    monkeypatch.setattr(gate,'now_utc',lambda:AFTER_MIDNIGHT)
    monkeypatch.setattr(sys,'argv',['gate','--delivery-date',day,'--publication-mode',mode,'--validation-only'])
    with pytest.raises(ValueError):gate.main()


@pytest.mark.parametrize('new_marker',[None,'matching','wrong-day','wrong-status'])
def test_original_receipt_compatibility_and_new_receipt_transaction_binding(monkeypatch,new_marker):
    artifact,responses,payload=trusted_receipt()
    run=json.loads(responses[0].getvalue());run['path']='.github/workflows/microcap-member-daily.yml'
    jobs=json.loads(responses[1].getvalue())
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        metadata=json.loads(archive.read('metadata.json'));receipt=json.loads(archive.read('smtp-accepted.json'))
    metadata['schema']='member_daily_report_v1';raw=json.dumps(metadata).encode()
    receipt['metadata_sha256']=hashlib.sha256(raw).hexdigest()
    marker=gate.delivery_marker_name(date(2026,11,2),'close_confirmed')
    artifact['name']=marker+'-smtp-accepted'
    if new_marker is not None:
        receipt['member_delivery_marker']=(marker if new_marker=='matching' else
            marker.replace('2026-11-02','2026-11-03') if new_marker=='wrong-day' else marker+'-partial-information')
    packed=io.BytesIO()
    with zipfile.ZipFile(packed,'w') as archive:
        archive.writestr('metadata.json',raw);archive.writestr('smtp-accepted.json',json.dumps(receipt))
    monkeypatch.setattr(gate,'now_utc',lambda:datetime.fromisoformat('2026-11-03T01:00:00+00:00'))
    responses=[io.BytesIO(json.dumps(v).encode()) for v in (run,jobs)]
    monkeypatch.setattr(gate.urllib.request,'urlopen',lambda *a,**k:responses.pop(0))
    monkeypatch.setattr(helper,'download',lambda *a:packed.getvalue())
    if new_marker in {None,'matching'}:
        gate.verify_smtp_receipt('o/r','software','https://api.github.com',artifact,date(2026,11,2),'close_confirmed')
    else:
        with pytest.raises(RuntimeError,match='transaction date/mode/status'):
            gate.verify_smtp_receipt('o/r','software','https://api.github.com',artifact,date(2026,11,2),'close_confirmed')


@pytest.mark.parametrize('status',['OK','PARTIAL'])
@pytest.mark.parametrize('has_marker',[False,True])
def test_receipt_cannot_recover_the_other_complete_or_partial_namespace(monkeypatch,status,has_marker):
    artifact,responses,payload=trusted_receipt()
    run=json.loads(responses[0].getvalue());run['path']='.github/workflows/microcap-member-daily.yml'
    jobs=json.loads(responses[1].getvalue())
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        metadata=json.loads(archive.read('metadata.json'));receipt=json.loads(archive.read('smtp-accepted.json'))
    metadata.update(schema='member_daily_report_v1',status=status)
    raw=json.dumps(metadata).encode()
    receipt['metadata_sha256']=hashlib.sha256(raw).hexdigest()
    normal=gate.delivery_marker_name(date(2026,11,2),'close_confirmed')
    correct=normal+('-partial-information' if status=='PARTIAL' else '')
    artifact['name']=normal+('' if status=='PARTIAL' else '-partial-information')+'-smtp-accepted'
    if has_marker:receipt['member_delivery_marker']=correct
    packed=io.BytesIO()
    with zipfile.ZipFile(packed,'w') as archive:
        archive.writestr('metadata.json',raw);archive.writestr('smtp-accepted.json',json.dumps(receipt))
    monkeypatch.setattr(gate,'now_utc',lambda:datetime.fromisoformat('2026-11-03T01:00:00+00:00'))
    responses=[io.BytesIO(json.dumps(v).encode()) for v in (run,jobs)]
    monkeypatch.setattr(gate.urllib.request,'urlopen',lambda *a,**k:responses.pop(0))
    monkeypatch.setattr(helper,'download',lambda *a:packed.getvalue())
    with pytest.raises(RuntimeError,match='artifact transaction differs'):
        gate.verify_smtp_receipt('o/r','software','https://api.github.com',artifact,date(2026,11,2),'close_confirmed')
