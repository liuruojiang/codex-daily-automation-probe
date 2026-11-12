"""Real-Git and native-module boundary tests; no production data or network.

The tiny native module fixtures exercise delegation/identity, not a substitute
for the real strategy's complete restore acceptance.
"""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import urllib.request
import zipfile

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import microcap_scoped_whole_recovery as recovery

RUNTIME='a'*40


def artifact(runtime=RUNTIME):
    return dict(id=31,name=recovery.artifact_name(runtime),expired=False,size_in_bytes=123,
        digest='sha256:'+'b'*64,created_at='2026-11-10T03:00:00Z',workflow_run=dict(id=21,head_sha='c'*40),
        archive_download_url=f'https://api.github.com/repos/{recovery.REPOSITORY}/actions/artifacts/31/zip')


def run():
    return dict(id=21,status='completed',conclusion='success',head_branch='main',head_sha='c'*40,
        path=recovery.WORKFLOW,repository=dict(full_name=recovery.REPOSITORY),head_repository=dict(full_name=recovery.REPOSITORY))


def jobs():
    return dict(jobs=[dict(id=11,run_id=21,status='completed',conclusion='success',steps=[
        dict(name=recovery.UPLOAD_STEP,status='completed',conclusion='success')])])


def fake_api(monkeypatch,*,item=None,workflow=None,job=None):
    item=artifact() if item is None else item
    workflow=run() if workflow is None else workflow
    job=jobs() if job is None else job
    calls=[]
    def read(url,token):
        assert token=='in-memory-test-secret'
        calls.append(url)
        if '/artifacts?' in url:return dict(artifacts=[item] if item else [])
        if '/jobs?' in url:return job
        return workflow
    monkeypatch.setattr(recovery,'api_json',read)
    return calls


def test_discovery_accepts_only_exact_runtime_successful_main_upload(monkeypatch):
    calls=fake_api(monkeypatch)
    result=recovery.fetch_latest('in-memory-test-secret',RUNTIME)
    assert result['id']==31 and result['producer_head_sha']=='c'*40
    assert len(calls)==3 and all(recovery.REPOSITORY in url for url in calls)


@pytest.mark.parametrize('conclusion', ['failure', 'cancelled', 'timed_out'])
def test_closed_server_archive_after_lost_upload_response_is_readonly_reconciled(monkeypatch, conclusion):
    item,workflow,job=artifact(),run(),jobs()
    workflow.update(conclusion=conclusion,event='workflow_dispatch')
    value=job['jobs'][0];value['conclusion']=conclusion
    value['steps']=[
        dict(name='Earn original formal rebalance and next-session handoff',status='completed',conclusion='success'),
        dict(name='Pack verified scoped whole state',status='completed',conclusion='success'),
        dict(name=recovery.UPLOAD_STEP,status='completed',conclusion=conclusion,started_at='2026-11-10T08:00:00Z')]
    fake_api(monkeypatch,item=item,workflow=workflow,job=job)
    result=recovery.fetch_latest('in-memory-test-secret',RUNTIME)
    assert result['id']==31 and result['producer_head_sha']==workflow['head_sha']
    assert result['publication_acceptance']=='closed_server_object_reconciled_after_publication_response_loss'
    assert workflow['conclusion']==conclusion


@pytest.mark.parametrize('fault',['unearned','no_pack','not_started','skipped','no_digest','wrong_event'])
def test_whole_response_loss_without_an_earned_closed_publication_remains_unaccepted(monkeypatch,fault):
    item,workflow,job=artifact(),run(),jobs()
    workflow.update(conclusion='failure',event='workflow_dispatch')
    value=job['jobs'][0];value['conclusion']='failure'
    steps=[dict(name='Earn original formal rebalance and next-session handoff',status='completed',conclusion='success'),
           dict(name='Pack verified scoped whole state',status='completed',conclusion='success'),
           dict(name=recovery.UPLOAD_STEP,status='completed',conclusion='failure',started_at='2026-11-10T08:00:00Z')]
    value['steps']=steps
    if fault=='unearned':steps[0]['conclusion']='failure'
    elif fault=='no_pack':steps[1]['conclusion']='failure'
    elif fault=='not_started':steps[-1].pop('started_at')
    elif fault=='skipped':steps[-1]['conclusion']='skipped'
    elif fault=='no_digest':item.pop('digest')
    else:workflow['event']='pull_request'
    fake_api(monkeypatch,item=item,workflow=workflow,job=job)
    assert recovery.fetch_latest('in-memory-test-secret',RUNTIME) is None


@pytest.mark.parametrize('fault',['runtime','branch','workflow','repository','head_repository','cancelled','unfinished','run_id','upload_failed','job_failed','job_wrong_run'])
def test_unapproved_artifact_producers_are_not_discovered(monkeypatch,fault):
    item,workflow,job=artifact(),run(),jobs()
    if fault=='runtime':item['name']=recovery.artifact_name('d'*40)
    elif fault=='branch':workflow['head_branch']='candidate'
    elif fault=='workflow':workflow['path']='.github/workflows/microcap-realtime-digest.yml'
    elif fault=='repository':workflow['repository']['full_name']='untrusted/repository'
    elif fault=='head_repository':workflow['head_repository']['full_name']='untrusted/fork'
    elif fault=='cancelled':workflow['conclusion']='cancelled'
    elif fault=='unfinished':workflow['status']='in_progress'
    elif fault=='run_id':workflow['id']=22
    elif fault=='upload_failed':job['jobs'][0]['steps'][0]['conclusion']='failure'
    elif fault=='job_failed':job['jobs'][0]['conclusion']='failure'
    elif fault=='job_wrong_run':job['jobs'][0]['run_id']=22
    fake_api(monkeypatch,item=item,workflow=workflow,job=job)
    assert recovery.fetch_latest('in-memory-test-secret',RUNTIME) is None


@pytest.mark.parametrize('fault',['url','size','digest','declared_head'])
def test_eligible_but_invalid_artifact_metadata_is_a_failure_not_a_miss(monkeypatch,fault):
    item=artifact()
    if fault=='url':item['archive_download_url']='https://untrusted.invalid/state.zip'
    elif fault=='size':item['size_in_bytes']=recovery.MAX_BUNDLE_BYTES+1
    elif fault=='digest':item['digest']='sha256:unknown'
    else:item['workflow_run']['head_sha']='e'*40
    fake_api(monkeypatch,item=item)
    with pytest.raises(recovery.RecoveryError):recovery.fetch_latest('in-memory-test-secret',RUNTIME)


def test_incomplete_api_pagination_cannot_become_a_fallback_miss(monkeypatch):
    monkeypatch.setattr(recovery,'api_json',lambda *a:dict(artifacts=[{}]*100))
    with pytest.raises(recovery.RecoveryError,match='pagination_incomplete'):
        recovery.fetch_latest('in-memory-test-secret',RUNTIME)


def test_redirect_removes_cross_origin_auth_and_rejects_http():
    handler=recovery.HTTPSAuthSafeRedirect()
    request=urllib.request.Request('https://api.github.com/example',headers={'Authorization':'Bearer private-value'})
    redirected=handler.redirect_request(request,None,302,'redirect',{},'https://release-assets.githubusercontent.com/signed')
    assert redirected.get_header('Authorization') is None
    with pytest.raises(recovery.RecoveryError,match='not_https'):
        handler.redirect_request(request,None,302,'redirect',{},'http://untrusted.invalid/signed')


@pytest.mark.parametrize('url',['http://github.com/liuruojiang/codex-daily-automation-probe/releases/download/tag/state.zip',
    'https://github.com/untrusted/repository/releases/download/tag/state.zip',
    'https://github.com/liuruojiang/microcap/releases/download/tag/state.zip',
    'https://github.com/liuruojiang/codex-daily-automation-probe/releases/download/tag/state.zip?token=value',
    'https://github.com/liuruojiang/codex-daily-automation-probe/releases/download/tag/%2estate.zip'])
def test_fallback_requires_exact_public_repository_asset(url):
    with pytest.raises(recovery.RecoveryError):recovery.validate_seed(url,'f'*64)


def whole_manifest(release):
    return dict(status='complete',ok=True,scope='whole_workspace_delivery',release_sha=release,expected_date='2026-11-09',
                inputs={'core.py':'a'*64},artifacts={'outputs/nav.csv':'b'*64},streams={'v2.0':dict(rows=2)})


def write_bundle(path,manifest,*,duplicate=False):
    with zipfile.ZipFile(path,'w') as archive:
        archive.writestr(recovery.MANIFEST,json.dumps(manifest).encode())
        if duplicate:
            with pytest.warns(UserWarning):archive.writestr(recovery.MANIFEST,json.dumps(manifest).encode())
    return path


@pytest.mark.parametrize('fault',['candidate','incomplete','not_ok','future','bad_release','empty_inputs','duplicate'])
def test_bundle_identity_rejects_nonformal_future_or_ambiguous_state(tmp_path,fault):
    manifest=whole_manifest('b'*40)
    if fault=='candidate':manifest['scope']='whole_workspace_candidate_validation'
    elif fault=='incomplete':manifest['status']='blocked'
    elif fault=='not_ok':manifest['ok']=False
    elif fault=='future':manifest['expected_date']='2026-11-11'
    elif fault=='bad_release':manifest['release_sha']='main'
    elif fault=='empty_inputs':manifest['inputs']={}
    bundle=write_bundle(tmp_path/'whole.zip',manifest,duplicate=fault=='duplicate')
    with pytest.raises(recovery.RecoveryError):recovery.bundle_identity(bundle,RUNTIME,'2026-11-10')


@pytest.fixture
def git_native(tmp_path):
    root=tmp_path/'native';(root/'scripts').mkdir(parents=True)
    def git(*args):
        return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.STDOUT).decode().strip()
    git('init','-q');git('config','user.email','test@example.invalid');git('config','user.name','Isolated recovery boundary')
    (root/'core.py').write_bytes(b'original economic model\n')
    # A small protocol fixture, not the full native validator: the helper must
    # call the native function and cannot approve a merely matching SHA label.
    (root/'scripts/top100_delivery.py').write_text('''from pathlib import Path
import subprocess
def _verify_release_files(root,release):
    for name in ('core.py','scripts/top100_delivery.py','scripts/top100_cloud_delivery.py'):
        blob=subprocess.check_output(['git','-C',str(root),'show',release+':'+name])
        if (Path(root)/name).read_bytes().replace(b'\\r\\n',b'\\n')!=blob.replace(b'\\r\\n',b'\\n'):
            raise ValueError('Native release byte mismatch')
    return release
''',encoding='utf-8')
    (root/'scripts/top100_cloud_delivery.py').write_text('''from pathlib import Path
import zipfile,json
def restore(root,bundle,expected):
    with zipfile.ZipFile(bundle) as archive:raw=archive.read('outputs/top100_delivery_manifest.json')
    target=Path(root)/'outputs/top100_delivery_manifest.json';target.parent.mkdir(exist_ok=True);target.write_bytes(raw)
    (Path(root)/'native-restore-called').write_text(expected)
    return dict(ok=True,expected_date=expected,restore_source='software_native_boundary_fixture')
''',encoding='utf-8')
    git('add','.');git('commit','-qm','exact runtime')
    runtime=git('rev-parse','HEAD');git('branch','runtime')
    (root/'release-note.md').write_text('source equivalent merge release')
    git('add','.');git('commit','-qm','source-equivalent release')
    release=git('rev-parse','HEAD');git('branch','equivalent')
    (root/'core.py').write_bytes(b'different model\n');git('add','.');git('commit','-qm','unreviewed model')
    different=git('rev-parse','HEAD');git('checkout','--detach',runtime)
    git('remote','add','origin',str(root))
    return root,runtime,release,different,git


def test_actual_git_release_alias_is_verified_without_manifest_relabelling(git_native,tmp_path):
    root,runtime,release,_,_=git_native
    bundle=write_bundle(tmp_path/'whole.zip',whole_manifest(release));original=bundle.read_bytes()
    work=tmp_path/'work';work.mkdir()
    report=recovery.restore_bundle(root,bundle,runtime,'2026-11-10',work)
    assert report['runtime_sha']==runtime and report['source_manifest_release_sha']==release
    assert report['restored_manifest_release_sha']==release
    assert report['historical_release_files_equivalent'] and report['restored']
    assert json.loads((root/recovery.MANIFEST).read_bytes())['release_sha']==release
    assert bundle.read_bytes()==original


def test_real_git_mismatched_release_never_invokes_native_restore(git_native,tmp_path):
    root,runtime,_,different,_=git_native
    bundle=write_bundle(tmp_path/'whole.zip',whole_manifest(different));work=tmp_path/'work';work.mkdir()
    with pytest.raises(recovery.RecoveryError,match='source-release_failed'):
        recovery.restore_bundle(root,bundle,runtime,'2026-11-10',work)
    assert not (root/'native-restore-called').exists()
    assert not (root/recovery.MANIFEST).exists()


def test_exact_historical_release_is_fetched_without_switching_runtime(git_native,tmp_path):
    origin,runtime,release,_,_=git_native
    clone=tmp_path/'isolated-clone'
    subprocess.run(['git','clone','-q','--no-local','--depth','1','--single-branch','--branch','runtime',str(origin),str(clone)],check=True)
    assert subprocess.run(['git','-C',str(clone),'cat-file','-e',release+'^{commit}'],capture_output=True).returncode!=0
    bundle=write_bundle(tmp_path/'whole.zip',whole_manifest(release));work=tmp_path/'work';work.mkdir()
    report=recovery.restore_bundle(clone,bundle,runtime,'2026-11-10',work)
    assert report['source_manifest_release_sha']==release
    assert subprocess.check_output(['git','-C',str(clone),'rev-parse','HEAD'],text=True).strip()==runtime


def recover_args(git_native,tmp_path):
    root,runtime,*_=git_native
    return dict(root=root,runtime_sha=runtime,not_after='2026-11-10',bundle=tmp_path/'result.zip',
                receipt=tmp_path/'receipt.json',token='in-memory-test-secret',
                fallback_url='https://github.com/'+recovery.REPOSITORY+'/releases/download/exact/whole.zip',
                fallback_sha256='f'*64)


def test_unknown_discovery_failure_never_uses_explicit_fallback(git_native,tmp_path,monkeypatch):
    args=recover_args(git_native,tmp_path);calls=[]
    def unavailable(*a):raise recovery.RecoveryError('github_api_http_403')
    monkeypatch.setattr(recovery,'fetch_latest',unavailable)
    monkeypatch.setattr(recovery,'stream_download',lambda *a,**kw:calls.append('fallback'))
    with pytest.raises(recovery.RecoveryError):recovery.recover(**args)
    receipt=json.loads(args['receipt'].read_bytes())
    assert calls==[] and receipt['fallback_used'] is False and receipt['error_code']=='github_api_http_403'
    assert 'in-memory-test-secret' not in args['receipt'].read_text()


def test_explicit_seed_is_used_only_after_complete_artifact_miss(git_native,tmp_path,monkeypatch):
    args=recover_args(git_native,tmp_path);release=git_native[2]
    content=write_bundle(tmp_path/'seed.zip',whole_manifest(release)).read_bytes()
    args['fallback_sha256']=hashlib.sha256(content).hexdigest()
    monkeypatch.setattr(recovery,'fetch_latest',lambda *a:None)
    def seed(url,path,**kwargs):
        assert url==args['fallback_url'] and kwargs['expected_sha']==args['fallback_sha256']
        Path(path).write_bytes(content)
    monkeypatch.setattr(recovery,'stream_download',seed)
    report=recovery.recover(**args)
    assert report['fallback_used'] and report['source']=='explicit_release_seed'
    assert report['fallback_reason']=='complete_discovery_no_eligible_artifact'
    assert report['source_manifest_release_sha']==release and report['restored']
    assert json.loads(args['receipt'].read_bytes())==report


def test_artifact_transport_or_native_failure_never_downgrades_to_seed(git_native,tmp_path,monkeypatch):
    args=recover_args(git_native,tmp_path);item=artifact(args['runtime_sha'])
    monkeypatch.setattr(recovery,'fetch_latest',lambda *a:dict(item,producer_head_sha='c'*40))
    def failed(*a):raise recovery.RecoveryError('download_digest_mismatch')
    monkeypatch.setattr(recovery,'download_artifact',failed)
    monkeypatch.setattr(recovery,'stream_download',lambda *a,**k:pytest.fail('must not fallback'))
    with pytest.raises(recovery.RecoveryError,match='digest_mismatch'):recovery.recover(**args)
    receipt=json.loads(args['receipt'].read_bytes())
    assert not receipt['fallback_used'] and receipt['source']=='published_scoped_whole_artifact'


@pytest.mark.parametrize('fault',['digest','short_body','oversize','existing'])
def test_bounded_stream_transport_rejects_changed_or_incomplete_downloads(tmp_path,monkeypatch,fault):
    body=b'bounded transport bytes'
    class Opener:
        def open(self,*a,**k):return io.BytesIO(body)
    monkeypatch.setattr(recovery.urllib.request,'build_opener',lambda *a:Opener())
    path=tmp_path/'download.zip'
    kwargs={}
    if fault=='digest':kwargs['expected_sha']='0'*64
    elif fault=='short_body':kwargs['expected_bytes']=len(body)+1
    elif fault=='oversize':monkeypatch.setattr(recovery,'MAX_BUNDLE_BYTES',len(body)-1)
    else:path.write_bytes(b'previous evidence')
    with pytest.raises(recovery.RecoveryError):recovery.stream_download('https://github.com/test',path,**kwargs)
    if fault=='existing':assert path.read_bytes()==b'previous evidence'


def test_bounded_stream_transport_verifies_exact_body_digest(tmp_path,monkeypatch):
    body=b'bounded transport bytes'
    class Opener:
        def open(self,*a,**k):return io.BytesIO(body)
    monkeypatch.setattr(recovery.urllib.request,'build_opener',lambda *a:Opener())
    expected=hashlib.sha256(body).hexdigest()
    result=recovery.stream_download('https://github.com/test',tmp_path/'download.zip',expected_sha=expected,expected_bytes=len(body))
    assert result==dict(sha256=expected,bytes=len(body))


@pytest.mark.parametrize('fault',['wrong_filename','extra_file','symlink','oversize'])
def test_artifact_outer_archive_cannot_install_another_payload(tmp_path,monkeypatch,fault):
    outer=tmp_path/'outer.zip'
    with zipfile.ZipFile(outer,'w') as archive:
        if fault=='symlink':
            info=zipfile.ZipInfo(recovery.BUNDLE_NAME);info.create_system=3;info.external_attr=0o120777<<16
            archive.writestr(info,b'../../outside')
        else:
            archive.writestr('different.zip' if fault=='wrong_filename' else recovery.BUNDLE_NAME,b'inner')
            if fault=='extra_file':archive.writestr('unexpected.py',b'code')
    def download(request,path,**kwargs):
        path.write_bytes(outer.read_bytes());return dict(sha256='f'*64,bytes=outer.stat().st_size)
    monkeypatch.setattr(recovery,'stream_download',download)
    if fault=='oversize':monkeypatch.setattr(recovery,'MAX_BUNDLE_BYTES',4)
    with pytest.raises(recovery.RecoveryError):recovery.download_artifact(artifact(),'test-token',tmp_path)
    assert not (tmp_path/recovery.BUNDLE_NAME).exists()
