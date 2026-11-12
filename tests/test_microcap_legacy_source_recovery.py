"""Software-only original artifact pairs; no market/SMTP/dispatch/native gates."""
import hashlib
import io
import json
from pathlib import Path
import sys
import zipfile

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import microcap_legacy_source_recovery as m
import restore_microcap_rebalance_recovery as normal
from prepare_microcap_local import encode


def zipped(files):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,raw in files.items():archive.writestr(name,raw)
    return stream.getvalue()


@pytest.fixture
def originals():
    symbols=[f'{i:06}' for i in range(1,301)]
    config=json.loads((Path(__file__).resolve().parents[1]/'config/microcap_local_producer_runtime.json').read_bytes())
    master=b'software source master\n';reference=b'original software reference\n'
    context=dict(runtime_sha=m.PRODUCER,target=m.TARGET,scope_sha256=m.SCOPE,
        master_sha256=hashlib.sha256(master).hexdigest(),observation_owner_date=m.TARGET,
        observation_floor=m.TARGET+'T09:00:00+08:00',native_sources=config['calculation_sources'],
        orchestrator_sources=m.CONTROLLERS,orchestrator_sha256=m.CONTROLLERS['maintain_microcap_local.py'])
    bootstrap=encode(dict(status='complete',runtime_sha=m.PRODUCER,source_root=m.SOURCE_ROOT+'/scoped-consumer',
        symbols=symbols,reference_proxy_sha256=hashlib.sha256(reference).hexdigest()))
    cache={'runtime/.microcap_index_cache/security_master.csv':master,'bootstrap.json':bootstrap,'reference-proxy.csv':reference}
    diagnostic={'scoped-schedule.json':encode(dict(date=m.TARGET,is_trading_day=True,is_rebalance=True)),
        'scoped-source-state/bootstrap.json':bootstrap,'scoped-source-state/reference-proxy.csv':reference,
        'scoped-source-state/runs/'+m.TARGET+'/worker-context.json':encode(dict(context=context,symbols=symbols,dispatch_id='e'*32)),
        'scoped-source-state/runs/'+m.TARGET+'/result.json':encode(dict(status='incomplete',target=m.TARGET,total=300,processed=300,
            complete=44,symbols=symbols,context=context,scope_sha256=m.SCOPE,metadata_status='waiting_for_notice_or_current_names')),
        'scoped-prepare/commands/maintain-original/process.json':encode(dict(exit_code=1,command=['python','-B',
            m.SOURCE_ROOT+'/automation/scripts/maintain_microcap_local.py','run','--as-of',m.TARGET]))}
    for index,symbol in enumerate(symbols):
        for stage in ('price','ratio','share'):
            key=symbol+'-'+stage;status='failed' if stage=='share' and index>=44 else 'complete'
            record=dict(key=key,context=context,status=status,started_at='2026-11-12T07:30:00+00:00')
            if status=='complete':
                name='.microcap_index_cache/software-source-evidence/'+key+'.json'
                raw=encode(dict(symbol=symbol,stage=stage,fixture=True,observed_at='2026-11-12T07:30:00+00:00'))
                cache['runtime/'+name]=raw;record['files']={name:hashlib.sha256(raw).hexdigest()}
            else:record['error']='SHARE_REQUEST_RETRY_EXHAUSTED: quote'
            diagnostic[f'scoped-source-state/runs/{m.TARGET}/{key}.json']=encode(record)
    name='.microcap_index_cache/current_st.csv';raw=b'original software name observation\n'
    cache['runtime/'+name]=raw
    diagnostic[f'scoped-source-state/runs/{m.TARGET}/current-names.json']=encode(dict(key='current-names',context=context,status='complete',files={name:hashlib.sha256(raw).hexdigest()}))
    diagnostic[f'scoped-source-state/runs/{m.TARGET}/notice.json']=encode(dict(key='notice',context=context,status='failed',error='original native boundary mismatch'))
    return dict(diagnostic=diagnostic,cache=cache,context=context)


def network(monkeypatch,originals,*,change_run=None,change_job=None,change_artifact=None,missing=None):
    base=f'https://api.github.com/repos/{m.REPOSITORY}/actions'
    run=dict(id=m.LEGACY_RUN,run_attempt=1,head_sha=m.LEGACY_HEAD,head_branch='main',event='workflow_dispatch',
        path=m.WORKFLOW,status='completed',conclusion='failure',repository={'full_name':m.REPOSITORY},head_repository={'full_name':m.REPOSITORY})
    run.update(change_run or {})
    job=dict(id=131031632406,name='scoped',run_id=m.LEGACY_RUN,run_attempt=1,head_sha=m.LEGACY_HEAD,status='completed',conclusion='failure',steps=[
        dict(name='Prepare current close sources for exactly 300 candidates',status='completed',conclusion='failure'),
        dict(name='Preserve scoped maintenance evidence',status='completed',conclusion='success'),
        dict(name='Preserve failed scoped producer sources',status='completed',conclusion='success'),
        *[dict(name=name,status='completed',conclusion='skipped') for name in (
            'Earn original formal rebalance and next-session handoff','Pack verified scoped whole state',
            'Publish verified member checkpoint','Publish verified scoped whole state')]])
    job.update(change_job or {})
    bodies={'diagnostics':zipped({m.DIAGNOSTIC_PREFIX+n:raw for n,raw in originals['diagnostic'].items()}),
            'producer':zipped(originals['cache'])}
    items=[]
    for role,(identity,name,_) in m.OBJECTS.items():
        body=bodies[role]
        item=dict(id=identity,name=name,expired=False,archive_download_url=f'{base}/artifacts/{identity}/zip',
            size_in_bytes=len(body),digest='sha256:'+hashlib.sha256(body).hexdigest(),
            workflow_run=dict(id=m.LEGACY_RUN,head_sha=m.LEGACY_HEAD,head_branch='main'))
        if change_artifact and change_artifact[0]==role:item.update(change_artifact[1])
        if role!=missing:items.append(item)
    reads=[];downloads=[]
    def api(url,token):
        reads.append(url)
        if url==f'{base}/runs/{m.LEGACY_RUN}':return run
        if f'/runs/{m.LEGACY_RUN}/attempts/1/jobs?' in url:return dict(jobs=[job])
        if f'/runs/{m.LEGACY_RUN}/artifacts?' in url:return dict(artifacts=items)
        raise AssertionError('Unrecognized legacy fixture API '+url)
    def download(request,path,*,expected_sha,expected_bytes):
        identity=int(request.full_url.split('/')[-2]);role=next(r for r,v in m.OBJECTS.items() if v[0]==identity)
        body=bodies[role];downloads.append(role)
        assert hashlib.sha256(body).hexdigest()==expected_sha and len(body)==expected_bytes
        Path(path).write_bytes(body);return dict(sha256=expected_sha,bytes=expected_bytes)
    monkeypatch.setattr(m,'api_json',api);monkeypatch.setattr(m,'stream_download',download)
    return reads,downloads


def recover(tmp_path):
    return m.recover(token='fixture',runtime_sha=m.CONSUMER,producer_sha=m.PRODUCER,
        not_after='2026-11-13',out=tmp_path/'received',issuer_head='f'*40)


def test_original_pair_preserves_all_json_clocks_and_644_closed_source_stages(originals,tmp_path,monkeypatch):
    _,downloads=network(monkeypatch,originals)
    result=recover(tmp_path);manifest=json.loads(Path(result['manifest']).read_bytes())
    assert result['restored'] and downloads==['diagnostics','producer']
    assert result['summary']['successful_stock_stages']==644 and result['summary']['failed_share_stages']==256
    assert manifest['manifest_origin']==m.ORIGIN and manifest['original_source_was_sealed'] is False
    assert manifest['producer_commit']==m.LEGACY_HEAD and manifest['source_run_id']==m.LEGACY_RUN
    assert manifest['manifest_issuer']['head_sha']=='f'*40 and 'sealed_at' not in manifest
    assert manifest['formal_state_archive'] is None and manifest['final_strategy_delivery'] is False
    for name,raw in originals['diagnostic'].items():assert (Path(result['manifest']).parent/name).read_bytes()==raw
    normal.verify_manifest(manifest,m.CONSUMER,m.PRODUCER,'2026-11-13')


@pytest.mark.parametrize('change',[dict(head_sha='a'*40),dict(head_branch='feature'),dict(run_attempt=2),
    dict(conclusion='success'),dict(event='schedule'),dict(status='in_progress'),dict(head_repository={'full_name':'other/repo'})])
def test_other_head_attempt_owner_or_execution_never_supplies_legacy(originals,tmp_path,monkeypatch,change):
    _,downloads=network(monkeypatch,originals,change_run=change)
    with pytest.raises(m.LegacyRecoveryError,match='frozen_source_run'):recover(tmp_path)
    assert not downloads


@pytest.mark.parametrize('missing',['diagnostics','producer'])
def test_unpaired_artifact_never_adopts_a_cache(originals,tmp_path,monkeypatch,missing):
    _,downloads=network(monkeypatch,originals,missing=missing)
    with pytest.raises(m.LegacyRecoveryError,match='paired_artifact_missing'):recover(tmp_path)
    assert not downloads


@pytest.mark.parametrize('bad',[dict(digest=None),dict(workflow_run={'id':1,'head_sha':m.LEGACY_HEAD,'head_branch':'main'}),
    dict(size_in_bytes=True),dict(archive_download_url='https://example.invalid/source.zip')])
def test_unknown_outer_identity_never_downloads(originals,tmp_path,monkeypatch,bad):
    _,downloads=network(monkeypatch,originals,change_artifact=('producer',bad))
    with pytest.raises(m.LegacyRecoveryError,match='artifact_identity'):recover(tmp_path)
    assert not downloads


def test_failed_preservation_or_formal_attempt_is_not_source_only_evidence(originals,tmp_path,monkeypatch):
    network(monkeypatch,originals,change_job=dict(steps=[dict(name='Prepare current close sources for exactly 300 candidates',status='completed',conclusion='failure')]))
    with pytest.raises(m.LegacyRecoveryError,match='step_evidence'):recover(tmp_path)


@pytest.mark.parametrize('fault',['changed_success_body','missing_success_body','bootstrap_conflict','escaped_cache','consumer_output','wrong_context'])
def test_open_or_conflicting_source_closures_never_create_a_bridge_manifest(originals,tmp_path,monkeypatch,fault):
    if fault=='changed_success_body':originals['cache']['runtime/.microcap_index_cache/software-source-evidence/000001-price.json']=b'changed'
    elif fault=='missing_success_body':del originals['cache']['runtime/.microcap_index_cache/software-source-evidence/000001-price.json']
    elif fault=='bootstrap_conflict':originals['cache']['bootstrap.json']=b'changed'
    elif fault=='escaped_cache':originals['cache']['../escape']=b'escape'
    elif fault=='consumer_output':originals['cache']['scoped-consumer/outputs/nav.csv']=b'forbidden'
    else:
        name=f'scoped-source-state/runs/{m.TARGET}/000001-price.json'
        obj=json.loads(originals['diagnostic'][name]);obj['context']=dict(obj['context'],target='2026-11-13')
        originals['diagnostic'][name]=encode(obj)
    network(monkeypatch,originals)
    with pytest.raises((m.LegacyRecoveryError,normal.RecoveryError)):recover(tmp_path)
    assert not (tmp_path/'received/scoped-recovery-manifest.json').exists()


def test_bridge_roles_cannot_be_changed_into_formal_or_consumer_adoption(originals,tmp_path,monkeypatch):
    network(monkeypatch,originals);result=recover(tmp_path);manifest=json.loads(Path(result['manifest']).read_bytes())
    manifest['formal_state_archive']=dict(path='scoped-delivery/formal-state.zip',sha256='a'*64,bytes=1)
    with pytest.raises((m.LegacyRecoveryError,normal.RecoveryError)):normal.verify_manifest(manifest,m.CONSUMER,m.PRODUCER,'2026-11-13')


def test_new_sealed_is_preferred_and_orphan_only_never_reads_legacy(tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(normal,'discover',lambda *a,**k:dict(id=1))
    monkeypatch.setattr(normal,'_recover_candidate',lambda **k:dict(restored=True,status='native_sealed'))
    monkeypatch.setattr(m,'recover',lambda **k:calls.append(k))
    assert normal.recover(runtime_sha=m.CONSUMER,producer_sha=m.PRODUCER,not_after=m.TARGET,out=tmp_path/'new',token='fixture')['status']=='native_sealed'
    monkeypatch.setattr(normal,'discover',lambda *a,**k:None)
    assert normal.recover(runtime_sha=m.CONSUMER,producer_sha=m.PRODUCER,not_after=m.TARGET,out=tmp_path/'orphan',token='fixture',orphan_only=True)['restored'] is False
    assert calls==[]


def test_new_sealed_miss_uses_only_the_frozen_source_bridge(tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(normal,'discover',lambda *a,**k:None)
    monkeypatch.setattr(m,'recover',lambda **k:(calls.append(k) or dict(restored=True,manifest_origin=m.ORIGIN)))
    result=normal.recover(runtime_sha=m.CONSUMER,producer_sha=m.PRODUCER,not_after=m.TARGET,out=tmp_path/'out',token='fixture')
    assert result['manifest_origin']==m.ORIGIN and len(calls)==1


@pytest.mark.parametrize('target',[m.TARGET,'2026-11-16'])
def test_real_private_admission_keeps_original_source_only_roles_and_failed_records(originals,tmp_path,monkeypatch,target):
    network(monkeypatch,originals);result=recover(tmp_path)
    root=tmp_path/'private';consumer=root/'scoped-consumer';producer=root/'scoped-source-state/runtime'
    consumer.mkdir(parents=True);producer.mkdir(parents=True)
    def git(argv,**kwargs):
        if argv[-2:]==['rev-parse','HEAD']:
            return (m.CONSUMER if str(consumer) in argv else m.PRODUCER)+'\n'
        raise AssertionError('Only Git role reads are allowed')
    monkeypatch.setattr(normal.subprocess,'check_output',git)
    args=dict(consumer=consumer,producer=producer,state=producer.parent,prepare_out=root/'scoped-prepare',
        delivery_out=root/'scoped-delivery',target=target,consumer_sha=m.CONSUMER,producer_sha=m.PRODUCER,
        run=lambda *a:pytest.fail('Source-only bridge must not restore or execute formal consumer gates'))
    admitted=normal.admit_resume(result['manifest'],**args)
    assert admitted['manifest_origin']==m.ORIGIN and admitted['original_source_was_sealed'] is False
    assert admitted['legacy_source_evidence']['head_sha']==m.LEGACY_HEAD
    assert admitted['consumer_partial_admitted'] is False and admitted['formal_archive_restored'] is False
    assert not (consumer/'outputs').exists()
    name=f'scoped-source-state/runs/{m.TARGET}/000300-share.json'
    assert (root/name).read_bytes()==originals['diagnostic'][name]
    assert json.loads((root/name).read_bytes())['status']=='failed'
    if target==m.TARGET:
        assert (producer.parent/'bootstrap.json').exists()
    else:
        assert not (producer.parent/'bootstrap.json').exists()
        assert not (args['prepare_out']/'commands').exists()
    assert normal.admit_resume(result['manifest'],**args)['admission_reused'] is True
