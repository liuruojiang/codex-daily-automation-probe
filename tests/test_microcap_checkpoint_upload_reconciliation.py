"""A lost response never hides a closed accepted checkpoint or causes a write."""
import copy,hashlib,io,json,sys,zipfile
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import restore_microcap_member_checkpoint as restore


@pytest.fixture
def accepted(tmp_path,monkeypatch):
    raw=(ROOT/'tests/fixtures/member_checkpoint_local_read_20261111/checkpoint-13838054871.zip').read_bytes()
    with zipfile.ZipFile(io.BytesIO(raw)) as z:cp=json.loads(z.read('checkpoint.json'))
    namespace='4774966da38f0b5af18617f73cc67c39c9e41a8e';head='a'*40
    name=restore.V2_PREFIX+namespace+'-'+cp['as_of_date']+'-current-'+cp['period']['sha256'][:12]
    item=dict(id=42,name=name,expired=False,size_in_bytes=len(raw),digest='sha256:'+hashlib.sha256(raw).hexdigest(),
        workflow_run=dict(id=7,head_sha=head,head_branch='main'),
        archive_download_url='https://api.github.com/repos/liuruojiang/codex-daily-automation-probe/actions/artifacts/42/zip')
    run=dict(id=7,status='completed',conclusion='failure',head_branch='main',head_sha=head,event='workflow_dispatch',
        path='.github/workflows/microcap-member-maintenance.yml',
        repository=dict(full_name='liuruojiang/codex-daily-automation-probe'),
        head_repository=dict(full_name='liuruojiang/codex-daily-automation-probe'))
    steps=[dict(name='Earn original formal rebalance and next-session handoff',conclusion='success'),
           dict(name='Describe verified scoped member checkpoint',conclusion='success'),
           dict(name='Publish verified member checkpoint',conclusion='failure',status='completed',started_at='2026-11-11T08:00:00Z')]
    calls=[]
    def get(url):
        calls.append(url)
        if '/artifacts?' in url:return dict(artifacts=[item])
        if '/runs/7/jobs?' in url:return dict(jobs=[dict(steps=steps)])
        if url.endswith('/runs/7'):return run
        raise AssertionError('Unexpected non-read request: '+url)
    monkeypatch.setattr(restore,'api_request',lambda url,token:url)
    monkeypatch.setattr(restore,'checkpoint_json',get)
    monkeypatch.setattr(restore,'download',lambda item,token:raw)
    return namespace,item,run,steps,calls,raw


@pytest.mark.parametrize('conclusion', ['failure', 'cancelled', 'timed_out'])
def test_accepted_checkpoint_after_response_loss_is_found_readonly_with_original_identity(accepted, conclusion):
    namespace,item,run,steps,calls,raw=accepted
    run['conclusion']=conclusion
    steps[-1]['conclusion']=conclusion
    result=restore.fetch_latest('liuruojiang/codex-daily-automation-probe','in-memory-only',namespace,'2026-11-11')
    assert result['id']==42 and result['workflow_run']['id']==7 and result['_payload']==raw
    assert result['_publication_acceptance']=='server_object_reconciled_after_upload_response_loss'
    assert result['_selection']['as_of_date']=='2026-11-11'
    assert not any('dispatch' in url for url in calls)


@pytest.mark.parametrize('fault',['not_started','skipped','describe_failed','native_failed','other_workflow','fork','other_head','wrong_event','no_digest'])
def test_failure_qualification_does_not_accept_an_unearned_or_unclosed_server_object(accepted,fault):
    namespace,item,run,steps,calls,raw=accepted
    if fault=='not_started':steps[-1].pop('started_at')
    elif fault=='skipped':steps[-1]['conclusion']='skipped'
    elif fault=='describe_failed':steps[1]['conclusion']='failure'
    elif fault=='native_failed':steps[0]['conclusion']='failure'
    elif fault=='other_workflow':run['path']='.github/workflows/microcap-member-daily.yml'
    elif fault=='fork':run['head_repository']['full_name']='another/fork'
    elif fault=='other_head':item['workflow_run']['head_sha']='b'*40
    elif fault=='wrong_event':run['event']='pull_request'
    else:item.pop('digest')
    with pytest.raises(RuntimeError,match='No published member checkpoint'):
        restore.fetch_latest('liuruojiang/codex-daily-automation-probe','in-memory-only',namespace,'2026-11-11')


def test_reconciliation_digest_failure_is_fatal_and_cannot_fall_back_to_an_old_seed(accepted):
    namespace,item,run,steps,calls,raw=accepted
    item['digest']='sha256:'+'0'*64
    with pytest.raises(ValueError,match='original size/digest differs'):
        restore.fetch_latest('liuruojiang/codex-daily-automation-probe','in-memory-only',namespace,'2026-11-11')
