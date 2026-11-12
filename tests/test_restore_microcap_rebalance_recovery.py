"""Closed partial recovery transport/admission; all HTTP/native calls are fixtures."""
import hashlib
import io
import json
from pathlib import Path
import stat
import sys
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import restore_microcap_rebalance_recovery as m
from prepare_microcap_local import encode, writer_lock

RUNTIME, PRODUCER, AUTO = 'a' * 40, 'b' * 40, 'c' * 40


def put(root, name, value):
    path = root / name; path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else encode(value))
    return path


@pytest.fixture
def origin(tmp_path):
    root = tmp_path / 'original'; root.mkdir()
    plan = dict(schema='microcap_scoped_recovery_plan_v1', consumer_sha=RUNTIME,
                delivery_date='2026-11-13', required_rebalance_dates=['2026-11-12'])
    put(root, 'scoped-recovery-plan.json', plan)
    put(root, 'scoped-prepare/boundaries/2026-11-12/page-01.json', b'original dated R12 body')
    put(root, 'scoped-prepare/boundaries/2026-11-12/page-01.receipt.json', dict(fetched_at='2026-11-16T01:00:00+00:00'))
    put(root, 'scoped-prepare/boundary-2026-11-12.json', dict(date='2026-11-12'))
    put(root, 'scoped-source-state/runtime/.microcap_index_cache/prices_raw/000001.csv', b'old exact source')
    put(root, 'scoped-source-state/runs/2026-11-13/000001-price.json', dict(status='complete'))
    put(root, 'scoped-source-state/bootstrap.json', dict(original_runtime=PRODUCER))
    put(root, 'scoped-prepare/prepared.json', dict(target='2026-11-13', consumer_root=str(root/'scoped-consumer')))
    return root


def seal(root, phase='state'):
    return m.seal(root=root, runtime_sha=RUNTIME, producer_sha=PRODUCER,
                  plan=root/'scoped-recovery-plan.json', out=root/m.MANIFESTS[phase],
                  phase=phase, run_id=123, run_attempt=2, producer_commit=AUTO)


def payload(root, manifest, changes=None):
    body = io.BytesIO()
    with zipfile.ZipFile(body, 'w') as archive:
        for name in [m.MANIFESTS[manifest['phase']], *manifest['files']]:
            archive.writestr(name, (changes or {}).get(name, (root/name).read_bytes()))
        for name, content in (changes or {}).items():
            if name not in manifest['files'] and name != m.MANIFESTS[manifest['phase']]:
                archive.writestr(name, content)
    return body.getvalue()


def server(monkeypatch, body, *, phase='state', run_change=None, artifact_change=None):
    run = dict(id=123, status='completed', conclusion='failure', head_branch='main',
               event='workflow_dispatch', path=m.WORKFLOW, head_sha=AUTO, run_attempt=2)
    run.update(run_change or {})
    item = dict(id=456, name=m.PREFIXES[phase]+RUNTIME, expired=False,
                size_in_bytes=len(body), digest='sha256:'+hashlib.sha256(body).hexdigest(),
                archive_download_url='https://api.github.com/repos/'+m.REPOSITORY+'/actions/artifacts/456/zip',
                created_at='2026-11-16T01:00:00Z', workflow_run=dict(id=123,head_sha=AUTO))
    item.update(artifact_change or {})
    reads=[]; downloads=[]
    def api(url,token):
        reads.append(url)
        if '/artifacts?' in url:
            return dict(artifacts=[item] if 'name='+m.PREFIXES[phase] in url else [])
        if url.endswith('/runs/123'):return run
        if '/jobs?' in url:return dict(jobs=[dict(steps=[dict(name=m.UPLOAD_STEPS[phase],status='completed',conclusion='success')])])
        raise AssertionError('Unexpected fixture API path')
    def download(request,destination,**kwargs):
        downloads.append(str(destination)); Path(destination).write_bytes(body)
        assert kwargs['expected_sha']==hashlib.sha256(body).hexdigest()
        assert kwargs['expected_bytes']==len(body)
        return dict(sha256=hashlib.sha256(body).hexdigest(),bytes=len(body))
    monkeypatch.setattr(m,'api_json',api);monkeypatch.setattr(m,'stream_download',download)
    return reads,downloads


def test_failed_main_run_is_only_partial_and_keeps_original_clocks(origin,tmp_path,monkeypatch):
    manifest=seal(origin); body=payload(origin,manifest); _,downloads=server(monkeypatch,body)
    out=tmp_path/'received'
    result=m.recover(runtime_sha=RUNTIME,producer_sha=PRODUCER,not_after='2026-11-16',out=out,token='fixture')
    assert result['restored'] is True and result['final_strategy_delivery'] is False
    assert result['native_adoption_required'] is True and result['source_run_id']==123
    assert len(downloads)==1
    for name,digest in manifest['files'].items():
        assert m.sha_file(out/name)==digest
    assert (out/'scoped-prepare/boundaries/2026-11-12/page-01.receipt.json').read_bytes()==(
        origin/'scoped-prepare/boundaries/2026-11-12/page-01.receipt.json').read_bytes()


def test_boundary_seal_cannot_promote_incomplete_prepared_or_sources(origin):
    result=seal(origin,'boundaries')
    assert all(m.allowed(path,'boundaries') for path in result['files'])
    assert 'scoped-prepare/prepared.json' not in result['files']
    assert not any(name.startswith('scoped-source-state/') for name in result['files'])


def test_formal_archive_is_preferred_without_duplicate_consumer_tree(origin):
    put(origin,'scoped-delivery/formal-state.zip',b'original native verified archive fixture')
    put(origin,'scoped-consumer/outputs/example.csv',b'no duplicate export')
    result=seal(origin)
    assert result['formal_state_archive']['sha256']==result['files']['scoped-delivery/formal-state.zip']
    assert not any(name.startswith('scoped-consumer/') for name in result['files'])


def test_sealed_source_cannot_be_overwritten_or_given_new_clock(origin):
    first=seal(origin); assert seal(origin)==first
    put(origin,'scoped-prepare/boundary-2026-11-12.json',dict(changed=True))
    with pytest.raises(m.RecoveryError,match='object_changed'):seal(origin)


@pytest.mark.parametrize('change', [dict(head_branch='feature'),dict(path='another.yml'),dict(event='pull_request')])
def test_untrusted_run_does_not_supply_recovery(origin,tmp_path,monkeypatch,change):
    manifest=seal(origin); _,downloads=server(monkeypatch,payload(origin,manifest),run_change=change)
    result=m.recover(runtime_sha=RUNTIME,producer_sha=PRODUCER,not_after='2026-11-16',out=tmp_path/'out',token='fixture')
    assert result['restored'] is False and downloads==[]


def test_inflight_source_does_not_fall_back_to_an_older_writer(origin,tmp_path,monkeypatch):
    manifest=seal(origin); _,downloads=server(monkeypatch,payload(origin,manifest),run_change=dict(status='in_progress'))
    with pytest.raises(m.RecoveryError,match='still_active'):
        m.recover(runtime_sha=RUNTIME,producer_sha=PRODUCER,not_after='2026-11-16',out=tmp_path/'out',token='fixture')
    assert downloads==[]


@pytest.mark.parametrize('change', [dict(digest=None),dict(digest='sha256:bad'),dict(archive_download_url='http://unknown/x'),dict(size_in_bytes=True)])
def test_wrong_artifact_identity_blocks_before_download(origin,tmp_path,monkeypatch,change):
    manifest=seal(origin); _,downloads=server(monkeypatch,payload(origin,manifest),artifact_change=change)
    with pytest.raises(m.RecoveryError):
        m.recover(runtime_sha=RUNTIME,producer_sha=PRODUCER,not_after='2026-11-16',out=tmp_path/'out',token='fixture')
    assert downloads==[]


@pytest.mark.parametrize('changes', [
    {'scoped-prepare/boundary-2026-11-12.json':b'changed'},
    {'extra-unbound.json':b'not declared'}, {'../outside':b'escape'},
])
def test_corrupt_or_unbound_objects_never_earn_an_admission_receipt(origin,tmp_path,monkeypatch,changes):
    manifest=seal(origin); server(monkeypatch,payload(origin,manifest,changes))
    out=tmp_path/'out'
    with pytest.raises((m.RecoveryError,KeyError)):
        m.recover(runtime_sha=RUNTIME,producer_sha=PRODUCER,not_after='2026-11-16',out=out,token='fixture')
    assert not (out/'recovery-receipt.json').exists()


def test_future_partial_target_cannot_be_used_as_current_recovery(origin,tmp_path,monkeypatch):
    manifest=seal(origin);server(monkeypatch,payload(origin,manifest))
    with pytest.raises(m.RecoveryError,match='future'):
        m.recover(runtime_sha=RUNTIME,producer_sha=PRODUCER,not_after='2026-11-12',out=tmp_path/'out',token='fixture')


def admission_args(tmp_path,monkeypatch,target='2026-11-13'):
    root=tmp_path/'new-private'; consumer=root/'scoped-consumer';producer=root/'scoped-source-state/runtime'
    consumer.mkdir(parents=True);producer.mkdir(parents=True)
    def git(argv,**kwargs):
        if argv[-2:]==['rev-parse','HEAD']:
            return (RUNTIME if str(consumer) in argv else PRODUCER)+'\n'
        raise m.subprocess.CalledProcessError(1,argv)
    monkeypatch.setattr(m.subprocess,'check_output',git)
    calls=[]
    args=dict(consumer=consumer,producer=producer,state=producer.parent,prepare_out=root/'scoped-prepare',
              delivery_out=root/'scoped-delivery',target=target,consumer_sha=RUNTIME,producer_sha=PRODUCER,
              run=lambda label,argv:calls.append((label,argv)))
    return args,calls


def test_private_admission_reuses_sources_and_never_rewrites_prepared_json(origin,tmp_path,monkeypatch):
    seal(origin);args,calls=admission_args(tmp_path,monkeypatch)
    result=m.admit_resume(origin/m.MANIFESTS['state'],**args)
    assert result['same_target'] and result['original_json_and_clocks_rewritten'] is False
    assert (args['prepare_out']/'prepared.json').read_bytes()==(origin/'scoped-prepare/prepared.json').read_bytes()
    assert result['roots']['consumer']==str(args['consumer'])
    assert result['path_admission'][str(origin/'scoped-consumer')]==str(args['consumer'])
    assert calls==[] and result['consumer_partial_admitted'] is False


def test_second_admission_does_not_restore_or_overwrite_newer_work(origin,tmp_path,monkeypatch):
    put(origin,'scoped-delivery/formal-state.zip',b'original archive fixture');seal(origin)
    args,calls=admission_args(tmp_path,monkeypatch)
    first=m.admit_resume(origin/m.MANIFESTS['state'],**args)
    fresh=args['producer']/'.microcap_index_cache/prices_raw/000001.csv';fresh.write_bytes(b'new successful work')
    second=m.admit_resume(origin/m.MANIFESTS['state'],**args)
    assert second['admission_reused'] is True and len(calls)==1
    assert fresh.read_bytes()==b'new successful work'


def test_cross_D_preserves_old_prepared_as_prior_and_only_reuses_sources_boundaries(origin,tmp_path,monkeypatch):
    seal(origin);args,_=admission_args(tmp_path,monkeypatch,target='2026-11-16')
    result=m.admit_resume(origin/m.MANIFESTS['state'],**args)
    assert result['same_target'] is False
    assert not (args['prepare_out']/'prepared.json').exists()
    assert (args['prepare_out']/'boundaries/2026-11-12/page-01.receipt.json').exists()
    assert not (args['state']/'bootstrap.json').exists()
    assert (args['state']/'runs/2026-11-13/000001-price.json').exists()


def test_living_worker_lock_prevents_partial_admission(origin,tmp_path,monkeypatch):
    seal(origin);args,_=admission_args(tmp_path,monkeypatch)
    with writer_lock(args['state']/'worker-slot-0.lock'):
        with pytest.raises((OSError,BlockingIOError,RuntimeError)):
            m.admit_resume(origin/m.MANIFESTS['state'],**args)
    assert not (args['producer']/'.microcap_index_cache').exists()


def test_changed_original_closure_is_detected_even_on_second_admission(origin,tmp_path,monkeypatch):
    seal(origin);args,_=admission_args(tmp_path,monkeypatch)
    m.admit_resume(origin/m.MANIFESTS['state'],**args)
    put(origin,'scoped-source-state/runs/2026-11-13/000001-price.json',dict(changed=True))
    with pytest.raises(m.RecoveryError,match='original_object_changed'):
        m.admit_resume(origin/m.MANIFESTS['state'],**args)


def successful_refresh(root, *, expected='2026-11-13', process_exit=0, completion_unconfirmed=False):
    raw=put(root,'scoped-consumer/.microcap_index_cache/earned.json',b'new exact native input')
    nav=put(root,'scoped-consumer/outputs/nav.csv',b'new exact native export')
    complete=dict(ok=True,status='complete',scope='whole_workspace_delivery',expected_date=expected,
                  inputs={'.microcap_index_cache/earned.json':m.sha_file(raw)},artifacts={'outputs/nav.csv':m.sha_file(nav)})
    final=put(root,'scoped-consumer/outputs/top100_delivery_manifest.json',complete)
    context=put(root,'scoped-delivery/stage-receipts/context.json',dict(target='2026-11-13',consumer_sha=RUNTIME))
    process=put(root,'scoped-delivery/commands/refresh/process.json',dict(exit_code=process_exit,completion_unconfirmed=completion_unconfirmed))
    stdout=put(root,'scoped-delivery/commands/refresh/stdout.log',b'original success log')
    stderr=put(root,'scoped-delivery/commands/refresh/stderr.log',b'')
    objects=[]
    for path in (raw,nav,final,process,stdout,stderr):
        role='consumer' if path.is_relative_to(root/'scoped-consumer') else 'out'
        base=root/('scoped-consumer' if role=='consumer' else 'scoped-delivery')
        objects.append(dict(role=role,path=path.relative_to(base).as_posix(),sha256=m.sha_file(path)))
    command_evidence={key:dict(role='out',path=path.relative_to(root/'scoped-delivery').as_posix())
                      for key,path in (('process',process),('stdout',stdout),('stderr',stderr))}
    put(root,'scoped-delivery/stage-receipts/refresh.json',dict(schema='microcap_scoped_successful_stage_v1',
        exit_code=0,context_sha256=m.sha_file(context),command=['python','{consumer}/scripts/top100_delivery.py','refresh-all'],
        objects=objects,command_evidence=command_evidence))


def test_completed_refresh_can_be_privately_admitted_but_still_requires_check(origin,tmp_path,monkeypatch):
    successful_refresh(origin);seal(origin);args,calls=admission_args(tmp_path,monkeypatch)
    result=m.admit_resume(origin/m.MANIFESTS['state'],**args)
    assert result['consumer_partial_admitted'] is True and result['native_check_required'] is True
    assert result['final_strategy_delivery'] is False and calls==[]
    assert (args['consumer']/'outputs/nav.csv').read_bytes()==b'new exact native export'


@pytest.mark.parametrize('fault',['wrong_target','failed_process','unknown_process','missing_output'])
def test_partial_consumer_never_promotes_unknown_or_open_native_closure(origin,tmp_path,monkeypatch,fault):
    successful_refresh(origin,expected='2026-11-12' if fault=='wrong_target' else '2026-11-13',
                       process_exit=1 if fault=='failed_process' else 0,
                       completion_unconfirmed=fault=='unknown_process')
    manifest=seal(origin)
    if fault=='missing_output':
        name='scoped-consumer/outputs/nav.csv';manifest['files'].pop(name);manifest['sizes'].pop(name)
        put(origin,m.MANIFESTS['state'],manifest)
    args,calls=admission_args(tmp_path,monkeypatch)
    with pytest.raises(m.RecoveryError):
        m.admit_resume(origin/m.MANIFESTS['state'],**args)
    assert not (args['consumer']/'outputs/nav.csv').exists() and calls==[]


def test_cross_D_does_not_install_old_successful_consumer_as_current(origin,tmp_path,monkeypatch):
    successful_refresh(origin);seal(origin);args,_=admission_args(tmp_path,monkeypatch,target='2026-11-16')
    result=m.admit_resume(origin/m.MANIFESTS['state'],**args)
    assert result['consumer_partial_admitted'] is False
    assert not (args['consumer']/'outputs/nav.csv').exists()


def test_native_normalized_manifest_still_preserves_raw_mixed_csv_bytes(origin,tmp_path,monkeypatch):
    successful_refresh(origin)
    path=origin/'scoped-consumer/outputs/nav.csv'
    raw=b'date,value\r\n2026-11-12,1\r\n2026-11-13,2\n';path.write_bytes(raw)
    formal_path=origin/'scoped-consumer/outputs/top100_delivery_manifest.json'
    formal=json.loads(formal_path.read_bytes());formal['artifacts']['outputs/nav.csv']=hashlib.sha256(raw.replace(b'\r\n',b'\n')).hexdigest()
    formal_path.write_bytes(encode(formal))
    receipt_path=origin/'scoped-delivery/stage-receipts/refresh.json';receipt=json.loads(receipt_path.read_bytes())
    for obj in receipt['objects']:
        if obj['role']=='consumer' and obj['path']=='outputs/nav.csv':obj['sha256']=m.sha_file(path)
        if obj['role']=='consumer' and obj['path']=='outputs/top100_delivery_manifest.json':obj['sha256']=m.sha_file(formal_path)
    receipt_path.write_bytes(encode(receipt));seal(origin)
    args,_=admission_args(tmp_path,monkeypatch)
    result=m.admit_resume(origin/m.MANIFESTS['state'],**args)
    assert result['consumer_partial_admitted'] is True
    assert (args['consumer']/'outputs/nav.csv').read_bytes()==raw


def test_recovery_can_fail_and_move_again_without_overwriting_its_original_admission(origin,tmp_path,monkeypatch):
    seal(origin);first_args,_=admission_args(tmp_path,monkeypatch)
    first=m.admit_resume(origin/m.MANIFESTS['state'],**first_args)
    intermediate=first_args['consumer'].parent
    original_report=(first_args['delivery_out']/'resume-admission.json').read_bytes()
    put(intermediate,'scoped-recovery-plan.json',(origin/'scoped-recovery-plan.json').read_bytes())
    seal(intermediate)
    later=tmp_path/'another-attempt';later.mkdir()
    second_args,_=admission_args(later,monkeypatch)
    second=m.admit_resume(intermediate/m.MANIFESTS['state'],**second_args)
    current=(second_args['delivery_out']/'resume-admission.json').read_bytes()
    old_copy=second_args['delivery_out']/('prior-admission-'+hashlib.sha256(original_report).hexdigest()+'.json')
    assert old_copy.read_bytes()==original_report and current!=original_report
    assert second['manifest_sha256']!=first['manifest_sha256']
    assert second['path_admission'][str(origin/'scoped-consumer')]==str(second_args['consumer'])


@pytest.mark.parametrize('fault',[None,'changed_stream','wrong_target'])
def test_native_pack_manifest_observation_does_not_invalidate_original_earned_manifest(origin,tmp_path,monkeypatch,fault):
    successful_refresh(origin)
    original_path=origin/'scoped-consumer/outputs/top100_delivery_manifest.json'
    first=json.loads(original_path.read_bytes())
    first.update(release_sha=RUNTIME,streams={'v2.0':{'last_date':'2026-11-13'}},verified_at='2026-11-13T07:00:00+00:00')
    original=encode(first);original_path.write_bytes(original)
    receipt_path=origin/'scoped-delivery/stage-receipts/refresh.json';refresh=json.loads(receipt_path.read_bytes())
    for obj in refresh['objects']:
        if obj['role']=='consumer' and obj['path']=='outputs/top100_delivery_manifest.json':obj['sha256']=hashlib.sha256(original).hexdigest()
    receipt_path.write_bytes(encode(refresh))
    put(origin,'scoped-delivery/formal-manifest-original.json',original)
    put(origin,'scoped-delivery/formal-state.zip',b'isolated native archive fixture')
    seal(origin);args,calls=admission_args(tmp_path,monkeypatch)
    packed=dict(first,verified_at='2026-11-13T07:01:00+00:00')
    if fault=='changed_stream':packed['streams']={'v2.0':{'last_date':'2026-11-12'}}
    if fault=='wrong_target':packed['expected_date']='2026-11-12'
    packed_raw=encode(packed)
    def restore(label,argv):
        calls.append((label,argv))
        for name in ('.microcap_index_cache/earned.json','outputs/nav.csv'):
            put(args['consumer'],name,(origin/'scoped-consumer'/name).read_bytes())
        put(args['consumer'],'outputs/top100_delivery_manifest.json',packed_raw)
    args['run']=restore
    if fault:
        with pytest.raises(m.RecoveryError,match='economic_closure_differs'):
            m.admit_resume(origin/m.MANIFESTS['state'],**args)
    else:
        result=m.admit_resume(origin/m.MANIFESTS['state'],**args)
        assert result['original_formal_manifest_restored'] is True
        assert (args['consumer']/'outputs/top100_delivery_manifest.json').read_bytes()==original
        saved=list(args['delivery_out'].glob('formal-manifest-packed-*.json'))
        assert len(saved)==1 and saved[0].read_bytes()==packed_raw
        again=m.admit_resume(origin/m.MANIFESTS['state'],**args)
        assert again['admission_reused'] is True and len(calls)==1
