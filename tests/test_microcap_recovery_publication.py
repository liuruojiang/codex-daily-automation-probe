"""Only finite checkpoint/whole uploads; API and native archive calls isolated."""
import hashlib
import io
import json
from pathlib import Path
import sys
import urllib.request
import zipfile

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import restore_microcap_rebalance_recovery as m
import microcap_scoped_whole_recovery as whole
from prepare_microcap_local import encode

RUNTIME,PRODUCER,AUTO,NAMESPACE='a'*40,'b'*40,'c'*40,'d'*40


def zipped(files):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as archive:
        for name,body in files.items():archive.writestr(name,body)
    return out.getvalue()


@pytest.fixture
def objects(tmp_path):
    cp=tmp_path/'handoff';cp.mkdir();period=encode(dict(anchor_day='2026-11-12'))
    (cp/'period.json').write_bytes(period)
    (cp/'checkpoint.json').write_bytes(encode(dict(as_of_date='2026-11-13',period_status='current',
        period=dict(path='period.json',sha256=hashlib.sha256(period).hexdigest()))))
    manifest=dict(ok=True,status='complete',scope='whole_workspace_delivery',expected_date='2026-11-13',
        release_sha=RUNTIME,inputs={'input': '1'*64},artifacts={'output':'2'*64},streams={'v2.0':{'rows':1}})
    bundle=tmp_path/'formal-state.zip';bundle.write_bytes(zipped({'outputs/top100_delivery_manifest.json':encode(manifest)}))
    return cp/'checkpoint.json',bundle


def network(monkeypatch,kind,objects,*,existing=False,corrupt=False):
    checkpoint,bundle=objects
    name,target,files,sizes,namespace=m._publication_payload(kind,checkpoint=checkpoint,bundle=bundle,
        namespace_sha=NAMESPACE,runtime_sha=RUNTIME)
    payload={path:(checkpoint.parent/path).read_bytes() for path in files} if kind=='checkpoint' else {whole.BUNDLE_NAME:bundle.read_bytes()}
    if corrupt:payload[next(iter(payload))]+=b'changed'
    outer=zipped(payload)
    bases='https://api.github.com/repos/'+m.REPOSITORY+'/actions'
    run=dict(id=10,status='completed',conclusion='failure',head_branch='main',event='workflow_dispatch',
        path=m.WORKFLOW,head_sha=AUTO,run_attempt=1,created_at='2026-11-13T07:05:00Z',
        repository={'full_name':m.REPOSITORY},head_repository={'full_name':m.REPOSITORY})
    publish='Publish verified member checkpoint' if kind=='checkpoint' else 'Publish verified scoped whole state'
    upstream=['Earn original formal rebalance and next-session handoff','Describe verified scoped member checkpoint'] if kind=='checkpoint' else ['Earn original formal rebalance and next-session handoff','Pack verified scoped whole state']
    job=dict(run_id=10,status='completed',conclusion='failure',steps=[dict(name=n,status='completed',conclusion='success') for n in upstream]+[
        dict(name=publish,status='completed',conclusion='failure',started_at='2026-11-13T07:06:00Z')])
    item=dict(id=100,name=name,expired=False,digest='sha256:'+hashlib.sha256(outer).hexdigest(),size_in_bytes=len(outer),
        archive_download_url=bases+'/artifacts/100/zip',workflow_run={'id':10,'head_sha':AUTO})
    state=dict(run=run,job=job,item=item,outer=outer,existing=existing,intent_item=None,intent_body=None)
    reads=[];downloads=[]
    def api(url,token):
        reads.append(url)
        if '/artifacts?' in url:
            if 'name=microcap-scoped-' in url and '-intent-v1-' in url:
                return {'artifacts':[state['intent_item']] if state['intent_item'] else []}
            return {'artifacts':[item] if state['existing'] else []}
        if url.endswith('/runs/10'):return state['run']
        if '/runs/10/jobs?' in url or '/runs/10/attempts/1/jobs?' in url:return {'jobs':[state['job']]}
        if url.endswith('/runs/20') or url.endswith('/runs/21'):
            return dict(run,id=int(url.rsplit('/',1)[1]),status='completed')
        if any(part in url for part in ('/runs/20/jobs?', '/runs/21/jobs?', '/runs/20/attempts/1/jobs?', '/runs/21/attempts/1/jobs?')):
            return {'jobs':[dict(steps=[dict(name=publish,status='completed',conclusion=state.get('old_publish','skipped'),
                started_at=None if state.get('old_publish','skipped')=='skipped' else '2026-11-13T07:06:00Z')])]}
        raise AssertionError('Unexpected publication fixture API path '+url)
    def download(request,destination,**kwargs):
        body=state['intent_body'] if '/artifacts/101/' in request.full_url else state['outer']
        assert hashlib.sha256(body).hexdigest()==kwargs['expected_sha'] and len(body)==kwargs['expected_bytes']
        downloads.append(request.full_url);Path(destination).write_bytes(body)
        return dict(sha256=hashlib.sha256(body).hexdigest(),bytes=len(body))
    monkeypatch.setattr(m,'api_json',api);monkeypatch.setattr(whole,'api_json',api)
    monkeypatch.setattr(m,'stream_download',download)
    def no_http(*a,**k):raise AssertionError('Real HTTP is forbidden in publication fixtures')
    monkeypatch.setattr(urllib.request,'urlopen',no_http)
    return state,reads,downloads


def invoke(kind,objects,tmp_path,run_id=20):
    checkpoint,bundle=objects
    return m.publication(kind=kind,intent=tmp_path/(kind+'-publication-intent.json'),token='fixture',
        run_id=run_id,run_attempt=1,producer_commit=AUTO,checkpoint=checkpoint,bundle=bundle,
        namespace_sha=NAMESPACE,runtime_sha=RUNTIME)


@pytest.mark.parametrize('kind',['checkpoint','whole'])
def test_server_accepted_but_upload_response_lost_is_read_only_no_second_upload(kind,objects,tmp_path,monkeypatch):
    _,reads,downloads=network(monkeypatch,kind,objects,existing=True)
    result=invoke(kind,objects,tmp_path)
    assert result['should_publish'] is False and result['artifact_id']==100
    if kind=='whole':
        assert result['acceptance']=='closed_server_object_reconciled_after_publication_response_loss'
    assert len(downloads)==1 and all('https://api.github.com/' in value for value in reads)
    assert not (tmp_path/(kind+'-publication-intent.json')).exists()


@pytest.mark.parametrize('kind',['checkpoint','whole'])
def test_first_upload_requires_persistent_exact_payload_intent(kind,objects,tmp_path,monkeypatch):
    network(monkeypatch,kind,objects)
    result=invoke(kind,objects,tmp_path)
    assert result['should_publish'] is True
    intent=json.loads(Path(result['intent']).read_bytes())
    assert intent['source_run_id']==20 and intent['producer_commit']==AUTO
    assert intent['desired_sha256']==result['desired_sha256']
    assert result['intent_artifact_name'].startswith('microcap-scoped-'+kind+'-intent-v1-')


@pytest.mark.parametrize('kind',['checkpoint','whole'])
def test_started_prior_write_without_server_object_never_authorizes_a_repeat(kind,objects,tmp_path,monkeypatch):
    state,_,_=network(monkeypatch,kind,objects)
    first=invoke(kind,objects,tmp_path);raw=Path(first['intent']).read_bytes();state['old_publish']='failure'
    with pytest.raises(m.RecoveryError,match='prior_write_unknown'):
        invoke(kind,objects,tmp_path,run_id=21)
    assert Path(first['intent']).read_bytes()==raw


def test_definitely_unstarted_old_upload_can_arm_one_new_attempt_preserving_old_intent(objects,tmp_path,monkeypatch):
    network(monkeypatch,'checkpoint',objects)
    first=invoke('checkpoint',objects,tmp_path);raw=Path(first['intent']).read_bytes()
    second=invoke('checkpoint',objects,tmp_path,run_id=21)
    assert first['intent']!=second['intent'] and Path(first['intent']).read_bytes()==raw


def test_durable_preupload_journal_survives_even_without_a_full_state_seal(objects,tmp_path,monkeypatch):
    state,_,_=network(monkeypatch,'whole',objects)
    first=invoke('whole',objects,tmp_path)
    original=json.loads(Path(first['intent']).read_bytes());original.update(source_run_id=10)
    raw=encode(original);body=zipped({'whole-publication-intent.json':raw})
    state['intent_body']=body
    state['intent_item']=dict(id=101,name=first['intent_artifact_name'],expired=False,
        digest='sha256:'+hashlib.sha256(body).hexdigest(),size_in_bytes=len(body),
        archive_download_url='https://api.github.com/repos/'+m.REPOSITORY+'/actions/artifacts/101/zip',
        workflow_run={'id':10,'head_sha':AUTO})
    separate=tmp_path/'next';separate.mkdir()
    with pytest.raises(m.RecoveryError,match='prior_write_unknown'):
        invoke('whole',objects,separate,run_id=21)
    assert not (separate/'whole-publication-intent.json').exists()


def orphan_manifest(tmp_path,bundle):
    root=tmp_path/'sealed';root.mkdir()
    plan=dict(schema='microcap_scoped_recovery_plan_v1',consumer_sha=RUNTIME,delivery_date='2026-11-13',required_rebalance_dates=['2026-11-12'])
    (root/'scoped-recovery-plan.json').write_bytes(encode(plan))
    (root/'scoped-delivery').mkdir();(root/'scoped-delivery/formal-state.zip').write_bytes(bundle.read_bytes())
    path=root/m.MANIFESTS['state']
    m.seal(root=root,runtime_sha=RUNTIME,producer_sha=PRODUCER,plan=root/'scoped-recovery-plan.json',out=path,
        phase='state',run_id=10,run_attempt=1,producer_commit=AUTO)
    return path


def test_checkpoint_already_done_can_publish_original_orphan_archive_without_new_sources(objects,tmp_path,monkeypatch):
    _,bundle=objects;network(monkeypatch,'whole',objects)
    manifest=orphan_manifest(tmp_path,bundle);calls=[]
    def native(root,archive,runtime,not_after,work):
        calls.append((archive,not_after))
        return dict(seed_date='2026-11-13',bundle_sha256=m.sha_file(archive),freshness='verified_archive_only_requires_current_refresh')
    monkeypatch.setattr(whole,'restore_bundle',native)
    result=m.orphan_publication(consumer=tmp_path/'consumer',manifest_path=manifest,runtime_sha=RUNTIME,
        producer_sha=PRODUCER,not_after='2026-11-16',out=tmp_path/'orphan',token='fixture',run_id=20,run_attempt=1,producer_commit=AUTO)
    assert result['should_publish'] is True and len(calls)==1
    assert result['preserved_bundle_sha256']==m.sha_file(bundle)
    assert result['new_market_data_collected'] is False and result['formal_refresh_repeated'] is False
    assert result['current_signal_ready'] is False


def test_already_accepted_orphan_whole_is_never_restored_or_uploaded_again(objects,tmp_path,monkeypatch):
    _,bundle=objects;network(monkeypatch,'whole',objects,existing=True)
    manifest=orphan_manifest(tmp_path,bundle)
    monkeypatch.setattr(whole,'restore_bundle',lambda *a:pytest.fail('Accepted original must not be restored again'))
    result=m.orphan_publication(consumer=tmp_path/'consumer',manifest_path=manifest,runtime_sha=RUNTIME,
        producer_sha=PRODUCER,not_after='2026-11-16',out=tmp_path/'orphan',token='fixture',run_id=20,run_attempt=1,producer_commit=AUTO)
    assert result['should_publish'] is False and result['artifact_id']==100
