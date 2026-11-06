"""Offline API/ZIP counterexamples for the member period restore race."""
import io
import json
import hashlib
from pathlib import Path
import sys
import zipfile
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
import restore_microcap_member_checkpoint as restore
from build_microcap_member_digest import REVISIONS

REPO='liuruojiang/codex-daily-automation-probe'
SHA='a'*40

def archive(day='2026-10-29',phase='current',offset=0):
    symbols=[f'{i+offset:06d}' for i in range(1,101)]
    period=dict(schema='member_period_v1',anchor_day='2026-10-29' if phase=='current' else '2026-10-15',effective_from='2026-10-30',
                effective_members=[dict(rank=i+1,symbol=s) for i,s in enumerate(symbols)])
    files={'period':('period.json',json.dumps(period).encode()),'history':('history.csv',f'date,microcap,hedge\n{day},1000,2000\n'.encode()),
           'turnover':('turnover.csv',b'rebalance_date,two_side_cost_rate\n'),
           'prices':('prices.json',json.dumps(dict(schema='member_price_basis_v1',as_of_date=day,rows=[dict(symbol=s,previous_raw_date=day,previous_raw_close=10) for s in symbols])).encode()),
           'version_state':('state.json',json.dumps(dict(versions={v:dict(date=day,strategy_revision=r) for v,r in REVISIONS.items()})).encode())}
    cp=dict(schema='member_daily_checkpoint_v1',as_of_date=day,period_status=phase)
    cp.update({k:dict(path=name,sha256=hashlib.sha256(raw).hexdigest()) for k,(name,raw) in files.items()})
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as z:
        z.writestr('checkpoint.json',json.dumps(cp))
        for name,raw in files.values():z.writestr(name,raw)
    name=f'{restore.V2_PREFIX}{SHA}-{day}-{phase}-{cp["period"]["sha256"][:12]}'
    return name,buffer.getvalue()

def install(monkeypatch,items):
    artifacts=[];bodies={}
    for identifier,(name,raw) in items:
        artifacts.append(dict(id=identifier,name=name,expired=False,workflow_run=dict(id=identifier),
            archive_download_url=f'https://api.github.com/repos/{REPO}/actions/artifacts/{identifier}/zip'))
        bodies[identifier]=raw
    def urlopen(request,**kwargs):
        url=request.full_url
        if '/artifacts?' in url:value=dict(artifacts=artifacts)
        elif '/jobs?' in url:value=dict(jobs=[dict(steps=[dict(name='Publish verified member checkpoint',conclusion='success')])])
        else:value=dict(head_branch='main',status='completed',path='.github/workflows/microcap-member-daily.yml')
        return io.BytesIO(json.dumps(value).encode())
    monkeypatch.setattr(restore.urllib.request,'urlopen',urlopen)
    monkeypatch.setattr(restore,'download',lambda artifact,token:bodies[artifact['id']])

def test_newer_upload_old_awaiting_cannot_undo_same_close_handoff(monkeypatch):
    install(monkeypatch,[(20,archive(phase='awaiting_handoff')),(10,archive())])
    selected=restore.fetch_latest(REPO,'isolated',SHA,'2026-10-29')
    assert selected['id']==10 and selected['_selection']['period_status']=='current'

def test_late_rebalance_upload_cannot_replace_next_session_close(monkeypatch):
    install(monkeypatch,[(20,archive()),(10,archive(day='2026-10-30',phase='awaiting_handoff'))])
    assert restore.fetch_latest(REPO,'isolated',SHA,'2026-10-30')['id']==10

def test_bad_payload_cannot_cover_previous_verified_state(monkeypatch,capsys):
    name,raw=archive(day='2026-10-30')
    install(monkeypatch,[(20,(name,b'invalid ZIP')),(10,archive())])
    chosen=restore.fetch_latest(REPO,'isolated',SHA,'2026-10-30')
    assert chosen['id']==10 and chosen['_rejected'][0]['artifact_id']==20
    assert 'Rejected malformed checkpoint' in capsys.readouterr().err

def test_future_checkpoint_name_is_not_selected(monkeypatch):
    install(monkeypatch,[(20,archive(day='2026-10-30')),(10,archive())])
    assert restore.fetch_latest(REPO,'isolated',SHA,'2026-10-29')['id']==10

def test_api_error_is_not_hidden_as_missing_artifact(monkeypatch):
    def fail(*args,**kwargs):raise OSError('isolated API failure')
    monkeypatch.setattr(restore.urllib.request,'urlopen',fail)
    with pytest.raises(OSError,match='API failure'):restore.fetch_latest(REPO,'isolated',SHA,'2026-10-29')

def test_conflicting_current_periods_require_explicit_reconciliation(monkeypatch):
    install(monkeypatch,[(20,archive(offset=100)),(10,archive())])
    with pytest.raises(RuntimeError,match='Conflicting'):restore.fetch_latest(REPO,'isolated',SHA,'2026-10-29')

def test_legacy_candidates_are_not_silently_truncated(monkeypatch):
    install(monkeypatch,[(i,('microcap-member-checkpoint-v1-'+SHA,archive()[1])) for i in range(9)])
    with pytest.raises(RuntimeError,match='ambiguous'):restore.fetch_latest(REPO,'isolated',SHA,'2026-10-29')


def changed_payload(pair,key,alter):
    name,raw=pair
    with zipfile.ZipFile(io.BytesIO(raw)) as z:files={p:z.read(p) for p in z.namelist()}
    cp=json.loads(files['checkpoint.json']);ref=cp[key];value=json.loads(files[ref['path']]);alter(value)
    files[ref['path']]=json.dumps(value).encode();ref['sha256']=hashlib.sha256(files[ref['path']]).hexdigest();files['checkpoint.json']=json.dumps(cp).encode()
    target=io.BytesIO()
    with zipfile.ZipFile(target,'w') as z:
        for filename,body in files.items():z.writestr(filename,body)
    return name,target.getvalue()


@pytest.mark.parametrize('key',['prices','version_state'])
def test_same_date_conflicting_raw_basis_or_model_state_is_not_latest_upload_wins(monkeypatch,key):
    def alter(value):
        if key=='prices':value['rows'][0]['previous_raw_close']=11
        else:value['versions']['v2.3']['next_holding']='long_microcap_short_zz1000'
    install(monkeypatch,[(20,changed_payload(archive(),key,alter)),(10,archive())])
    with pytest.raises(RuntimeError,match='Conflicting'):restore.fetch_latest(REPO,'isolated',SHA,'2026-10-29')


def test_invalid_decimal_payload_is_rejected_without_hiding_the_valid_previous_state(monkeypatch,capsys):
    name,raw=archive(day='2026-10-30')
    with zipfile.ZipFile(io.BytesIO(raw)) as z:files={p:z.read(p) for p in z.namelist()}
    cp=json.loads(files['checkpoint.json']);files['history.csv']=b'date,microcap,hedge\n2026-10-30,bad,2000\n';cp['history']['sha256']=hashlib.sha256(files['history.csv']).hexdigest();files['checkpoint.json']=json.dumps(cp).encode()
    target=io.BytesIO()
    with zipfile.ZipFile(target,'w') as z:
        for filename,body in files.items():z.writestr(filename,body)
    install(monkeypatch,[(20,(name,target.getvalue())),(10,archive())])
    assert restore.fetch_latest(REPO,'isolated',SHA,'2026-10-30')['id']==10
    assert 'Rejected malformed checkpoint' in capsys.readouterr().err
