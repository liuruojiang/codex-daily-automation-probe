"""Small software artifact pairs, not an actual D12 source/native acceptance.

The software bodies replace fixed outer hashes explicitly. Production keeps the
real run, both full-archive SHAs, old JSON SHAs and original observation clocks.
No test performs HTTP, source collection, native refresh, SMTP or publication.
"""
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import microcap_completed_prepare_recovery as m
import microcap_legacy_source_recovery as legacy
import restore_microcap_rebalance_recovery as normal
import run_microcap_scoped_rebalance as driver
from prepare_microcap_local import encode


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def zipped(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
    return stream.getvalue()


@pytest.fixture
def case(monkeypatch):
    symbols = [f'{i:06}' for i in range(1, 301)]
    master = b'original software master\n'; reference = b'original software reference\n'
    cache = {'runtime/.microcap_index_cache/security_master.csv': master}
    context = dict(runtime_sha=m.PRODUCER, target=m.TARGET, scope_sha256=m.SCOPE,
        master_sha256=digest(master), observation_owner_date=m.TARGET,
        observation_floor=m.TARGET+'T09:00:00+08:00', native_sources=m._producer_profile()['calculation_sources'],
        orchestrator_sources=m.CONTROLLERS, orchestrator_sha256=m.CONTROLLERS['maintain_microcap_local.py'])
    bootstrap = encode(dict(status='complete', runtime_sha=legacy.PRODUCER,
        source_root=m.SOURCE_ROOT+'/scoped-consumer', symbols=symbols, reference_proxy_sha256=digest(reference)))
    cache.update({'bootstrap.json': bootstrap, 'reference-proxy.csv': reference})
    prefix = f'scoped-source-state/runs/{m.TARGET}/'
    result = encode(dict(schema='microcap_scoped_maintenance_v1', status='complete', target=m.TARGET,
        total=300, processed=300, complete=300, failures={}, symbols=symbols, context=context,
        scope_sha256=m.SCOPE, reused_stages=644, missing_initialized_symbols=[], notice_status='complete',
        bootstrap_status='complete', metadata_status='complete', current_names_status='complete'))
    diagnostic = {'scoped-source-state/bootstrap.json': bootstrap, 'scoped-source-state/reference-proxy.csv': reference,
        prefix+'worker-context.json': encode(dict(symbols=symbols, context=context)), prefix+'result.json': result,
        'scoped-recovery-plan.json': encode(dict(schema='microcap_scoped_recovery_plan_v1',
            consumer_sha=m.CONSUMER, delivery_date=m.TARGET, required_rebalance_dates=[m.TARGET])),
        'scoped-schedule.json': encode(dict(date=m.TARGET, is_trading_day=True, is_rebalance=True)),
        'scoped-prepare/commands/maintain-original/process.json': encode(dict(exit_code=0,
            command=['python', '-B', m.SOURCE_ROOT+'/automation/scripts/maintain_microcap_local.py',
                     'run', '--as-of', m.TARGET], started_at='2026-11-12T15:01:00+00:00',
            completed_at='2026-11-12T15:09:59+00:00')),
        # These original failed-consumer objects must stay in the outer ZIP.
        'scoped-delivery/stage-receipts/context.json': encode(dict(consumer_sha=m.CONSUMER, target=m.TARGET)),
        'scoped-delivery/commands/refresh-original/process.json': encode(dict(exit_code=1)),
        'scoped-delivery/runner.lock': b'held original lock'}
    portable = {}
    def record(key, bodies):
        files = {}
        for name, raw in bodies.items():
            cache['runtime/'+name] = raw; files[name] = digest(raw)
        diagnostic[prefix+key+'.json'] = encode(dict(key=key, status='complete', context=context,
            started_at='2026-11-12T15:01:00+00:00', finished_at='2026-11-12T15:09:00+00:00', files=files))
    for symbol in symbols:
        base = '.microcap_index_cache/'
        groups = {
            'price': {base+'prices_raw/'+symbol+'.csv': b'date,close\n2026-11-12,1\n',
                      base+'trade_status/'+symbol+'.json': encode(dict(symbol=symbol, fixture=True))},
            'share': {base+'share_change/'+symbol+'.csv': b'date,total_shares\n2026-11-12,1\n',
                      base+'share_sources/'+symbol+'.json': encode(dict(symbol=symbol, fixture=True))},
            'ratio': {base+'return_basis/manifests/'+symbol+'.json': encode(dict(symbol=symbol, fixture=True)),
                      base+'return_basis/prices_adj/'+symbol+'.csv': b'date,close\n2026-11-12,1\n'},
        }
        for stage, bodies in groups.items():
            record(symbol+'-'+stage, bodies); portable.update(bodies)
    record('notice', {'.microcap_index_cache/software_notice.json': b'original software notice'})
    record('current-names', {'.microcap_index_cache/local_current_names/'+m.TARGET+'.json': b'original software names'})
    record('metadata', {'.microcap_index_cache/security_meta/'+s+'.json': encode(dict(symbol=s)) for s in symbols})
    manifest = dict(schema='microcap_scoped_source_inputs_v1', runtime_sha=m.PRODUCER, target=m.TARGET,
        producer_context=context, symbols=symbols, scope_sha256=m.SCOPE,
        source_inputs_only=True, native_ST_refresh_required_at_consumer=True, final_strategy_delivery=False,
        master_sha256=digest(master), files={n:digest(b) for n,b in portable.items()},
        evidence={'maintenance-evidence/master.csv':digest(master)})
    bundle = zipped(dict(portable, **{'maintenance-evidence/master.csv':master, 'manifest.json':encode(manifest)}))
    warm = b'original software warm ZIP bytes; not a new observation'
    config = encode(dict(schema='microcap_native_scoped_consumer_v1', source_root=m.SOURCE_ROOT+'/scoped-source-state/runtime',
        bundle=m.SOURCE_ROOT+'/scoped-prepare/sources.zip', bundle_sha256=digest(bundle), boundary_snapshots={},
        producer_metadata_receipt=m.SOURCE_ROOT+'/scoped-prepare/receipts/metadata-original.json'))
    metadata = diagnostic[prefix+'metadata.json']
    prepare_files = {'sources.zip':bundle, 'consumer-config.json':config, 'local-source-warm.zip':warm,
                     'receipts/metadata-original.json':metadata}
    prepared = encode(dict(schema='microcap_scoped_rebalance_prepared_v1', status='prepared_sources_only',
        target=m.TARGET, consumer_sha=m.CONSUMER, consumer_root=m.SOURCE_ROOT+'/scoped-consumer',
        producer_root=m.SOURCE_ROOT+'/scoped-source-state/runtime', scope_sha256=m.SCOPE, issuer_count=300,
        consumer_config='consumer-config.json', files={n:digest(raw) for n,raw in prepare_files.items()}))
    diagnostic.update({'scoped-prepare/'+name:raw for name,raw in prepare_files.items() if name!='local-source-warm.zip'})
    diagnostic['scoped-prepare/prepared.json'] = prepared
    for attr, raw in [('RESULT_SHA',result), ('PREPARED_SHA',prepared), ('SOURCES_SHA',bundle),
                      ('CONFIG_SHA',config), ('WARM_SHA',warm)]:
        monkeypatch.setattr(m, attr, digest(raw))
    monkeypatch.setattr(m, 'WARM_BYTES', len(warm))
    return dict(diagnostic=diagnostic, cache=cache, warm=warm, prepared=prepared, config=config,
                context=context, prefix=prefix, symbols=symbols)


def network(monkeypatch, case, *, mutate_run=None, mutate_job=None, mutate_artifact=None,
            missing=None, expired=None, warm_changes=None):
    monkeypatch.setenv('GITHUB_SHA', 'f'*40)  # Explicit software receiver, distinct from the frozen source.
    base = f'https://api.github.com/repos/{m.REPOSITORY}/actions'
    run = dict(id=m.SOURCE_RUN, run_attempt=1, head_sha=m.SOURCE_HEAD, head_branch='main', event='workflow_dispatch',
        path=m.WORKFLOW, status='completed', conclusion='failure', repository={'full_name':m.REPOSITORY},
        head_repository={'full_name':m.REPOSITORY})
    run.update(mutate_run or {})
    expected = {'Prepare current close sources for exactly 300 candidates':'success',
        'Earn original formal rebalance and next-session handoff':'failure',
        'Seal resumable scoped recovery state':'failure', 'Preserve resumable scoped recovery state':'skipped',
        'Preserve scoped maintenance evidence':'success', 'Preserve failed scoped producer sources':'success',
        'Pack verified scoped whole state':'skipped', 'Publish verified member checkpoint':'skipped',
        'Publish verified scoped whole state':'skipped'}
    job = dict(id=m.SOURCE_JOB, name='scoped', run_id=m.SOURCE_RUN, run_attempt=1, head_sha=m.SOURCE_HEAD,
        status='completed', conclusion='failure', steps=[dict(name=n, status='completed', conclusion=c) for n,c in expected.items()])
    job.update(mutate_job or {})
    bodies = {'diagnostics':zipped({m.DIAGNOSTIC_PREFIX+n:raw for n,raw in case['diagnostic'].items()}),
              'producer':zipped(case['cache']), 'warm':case['warm']}
    objects = {r:(v[0],v[1],len(bodies[r]),digest(bodies[r])) for r,v in m.OBJECTS.items()}
    monkeypatch.setattr(m, 'OBJECTS', objects)
    artifacts = []
    for role, (identity,name,size,sha) in objects.items():
        item = dict(id=identity, name=name, expired=role==expired, size_in_bytes=size, digest='sha256:'+sha,
            archive_download_url=f'{base}/artifacts/{identity}/zip',
            workflow_run=dict(id=m.SOURCE_RUN, head_sha=m.SOURCE_HEAD, head_branch='main'))
        if mutate_artifact and mutate_artifact[0]==role:
            item.update(mutate_artifact[1])
        if role!=missing:
            artifacts.append(item)
    warm = dict(id=m.WARM_ID, url=f'https://api.github.com/repos/{m.REPOSITORY}/releases/assets/{m.WARM_ID}',
        name=m.WARM_NAME, state='uploaded', size=m.WARM_BYTES, digest='sha256:'+m.WARM_SHA, browser_download_url=m.WARM_URL)
    warm.update(warm_changes or {})
    reads = []; downloads = []
    def api(url, token):
        reads.append(url)
        if url == f'{base}/runs/{m.SOURCE_RUN}':
            return run
        if f'/runs/{m.SOURCE_RUN}/attempts/1/jobs?' in url:
            return dict(jobs=[job])
        if f'/runs/{m.SOURCE_RUN}/artifacts?' in url:
            return dict(artifacts=artifacts)
        if url == warm['url']:
            return warm
        raise AssertionError('Unexpected software API '+url)
    def download(request, path, *, expected_sha, expected_bytes):
        role = 'warm' if '/releases/assets/' in request.full_url else next(
            r for r,v in objects.items() if '/artifacts/'+str(v[0])+'/zip' in request.full_url)
        raw = bodies[role]; downloads.append(role)
        assert digest(raw)==expected_sha and len(raw)==expected_bytes
        if role=='warm':
            assert request.get_header('Accept')=='application/octet-stream'
        Path(path).parent.mkdir(parents=True, exist_ok=True); Path(path).write_bytes(raw)
    monkeypatch.setattr(m, 'api_json', api); monkeypatch.setattr(m, 'stream_download', download)
    return reads, downloads


def recover(tmp_path):
    return m.recover(token='software', runtime_sha=m.READER_CONSUMER, producer_sha=m.PRODUCER,
        not_after=m.TARGET, out=tmp_path/'received', issuer_head='f'*40)


def private_args(tmp_path, monkeypatch, name='receiver'):
    root = tmp_path/name; consumer=root/'scoped-consumer'; producer=root/'scoped-source-state/runtime'
    consumer.mkdir(parents=True); producer.mkdir(parents=True)
    def git(argv, **kwargs):
        assert argv[-2:]==['rev-parse','HEAD'], 'No native/HTTP subprocess is permitted by this fixture'
        return (m.READER_CONSUMER if str(consumer) in argv else m.PRODUCER)+'\n'
    monkeypatch.setattr(normal.subprocess, 'check_output', git)
    # Only checkout fingerprint transport is software-bound here. The actual
    # fixed Git diff/native-source gate is exercised by the real cloud reader.
    monkeypatch.setattr(m, '_reader_checkout', lambda *a:None)
    return dict(consumer=consumer, producer=producer, state=producer.parent, prepare_out=root/'scoped-prepare',
        delivery_out=root/'scoped-delivery', target=m.TARGET, consumer_sha=m.READER_CONSUMER,
        producer_sha=m.PRODUCER, run=lambda *a:pytest.fail('Source bridge cannot run formal/native/HTTP commands'))


def test_completed_903_raw_stages_and_original_prepared_are_recovered_without_old_delivery(case, tmp_path, monkeypatch):
    _, downloads=network(monkeypatch,case); result=recover(tmp_path)
    value=json.loads(Path(result['manifest']).read_bytes()); saved=Path(result['manifest']).parent
    assert downloads==['diagnostics','producer','warm']
    assert result['summary']['successful_stages']==903 and result['summary']['complete_issuers']==300
    assert value['runtime_sha']==m.CONSUMER and value['producer_commit']==m.SOURCE_HEAD and 'sealed_at' not in value
    assert value['source_inputs_only'] and value['formal_state_archive'] is None
    assert (saved/'scoped-prepare/prepared.json').read_bytes()==case['prepared']
    assert (saved/'scoped-prepare/local-source-warm.zip').read_bytes()==case['warm']
    assert not (saved/'scoped-delivery').exists() and not (saved/'scoped-consumer').exists()
    assert not any(n.startswith('scoped-delivery/') for n in value['files'])
    for name, raw in case['diagnostic'].items():
        if name.startswith('scoped-source-state/'):
            assert (saved/name).read_bytes()==raw
    args=private_args(tmp_path,monkeypatch)
    admitted=normal.admit_resume(result['manifest'],**args)
    assert admitted['consumer_partial_admitted'] is False and admitted['formal_archive_restored'] is False
    assert admitted['prepared_source_reader']['reader_consumer_sha']==m.READER_CONSUMER
    assert admitted['prepared_source_reader']['native_source_import_required'] is True
    assert not (args['delivery_out']/'stage-receipts/context.json').exists()
    actual,_=driver.validate_prepared(args['prepare_out']/'prepared.json',args['consumer'],m.TARGET,m.READER_CONSUMER,admitted)
    assert actual['consumer_sha']==m.CONSUMER
    assert normal.admit_resume(result['manifest'],**args)['admission_reused']


@pytest.mark.parametrize('change',[dict(head_sha='a'*40),dict(run_attempt=2),dict(head_branch='other'),
    dict(status='in_progress'),dict(conclusion='success'),dict(event='schedule'),dict(head_repository={'full_name':'wrong/repo'})])
def test_run_head_attempt_and_ownership_are_exact(case,tmp_path,monkeypatch,change):
    _,downloads=network(monkeypatch,case,mutate_run=change)
    with pytest.raises(m.CompletedPrepareRecoveryError,match='frozen_source_run'):recover(tmp_path)
    assert not downloads


@pytest.mark.parametrize('bad',[dict(id=1),dict(digest='sha256:'+'0'*64),dict(size_in_bytes=True),
    dict(workflow_run={'id':m.SOURCE_RUN+1,'head_sha':m.SOURCE_HEAD,'head_branch':'main'})])
def test_same_named_artifact_is_not_a_general_fallback(case,tmp_path,monkeypatch,bad):
    _,downloads=network(monkeypatch,case,mutate_artifact=('producer',bad))
    with pytest.raises(m.CompletedPrepareRecoveryError,match='artifact_identity'):recover(tmp_path)
    assert not downloads


def test_unknown_expiry_is_not_a_confirmed_retention_miss(case,tmp_path,monkeypatch):
    _,downloads=network(monkeypatch,case,mutate_artifact=('producer',dict(expired=None)))
    with pytest.raises(m.CompletedPrepareRecoveryError,match='expiry_unknown'):recover(tmp_path)
    assert not downloads


@pytest.mark.parametrize('role',['diagnostics','producer'])
@pytest.mark.parametrize('state',['missing','expired'])
def test_explicit_retention_miss_does_not_block_future_normal_runs(case,tmp_path,monkeypatch,role,state):
    _,downloads=network(monkeypatch,case,**{state:role})
    assert recover(tmp_path)['restored'] is False and not downloads


@pytest.mark.parametrize('fault',['source_body','source_missing','old_context','failed_stage','metadata_299','prepared_tamper'])
def test_broken_source_closure_cannot_be_renamed_completed(case,tmp_path,monkeypatch,fault):
    key=case['prefix']+'000001-share.json'
    if fault=='source_body':case['cache']['runtime/.microcap_index_cache/share_change/000001.csv']=b'changed'
    elif fault=='source_missing':del case['cache']['runtime/.microcap_index_cache/share_change/000001.csv']
    elif fault=='prepared_tamper':case['diagnostic']['scoped-prepare/prepared.json']+=b' '
    else:
        if fault=='metadata_299':key=case['prefix']+'metadata.json'
        value=json.loads(case['diagnostic'][key])
        if fault=='old_context':value['context']=dict(value['context'],observation_floor='2026-11-13T09:00:00+08:00')
        elif fault=='failed_stage':value['status']='failed'
        else:value['files'].pop('.microcap_index_cache/security_meta/000300.json')
        case['diagnostic'][key]=encode(value)
    network(monkeypatch,case)
    with pytest.raises(m.CompletedPrepareRecoveryError):recover(tmp_path)
    assert not (tmp_path/'received/scoped-recovery-manifest.json').exists()


def test_missing_warm_is_restored_only_from_original_published_identity(case,tmp_path,monkeypatch):
    _,downloads=network(monkeypatch,case,warm_changes={'digest':'sha256:'+'0'*64})
    with pytest.raises(m.CompletedPrepareRecoveryError,match='warm_server_identity'):recover(tmp_path)
    assert not downloads


def test_new_seal_precedes_bridge_and_orphan_discovery_never_uses_source_bridge(tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(normal,'discover',lambda *a,**k:dict(id=1))
    monkeypatch.setattr(normal,'_recover_candidate',lambda **k:dict(restored=True,status='new_sealed'))
    monkeypatch.setattr(m,'recover',lambda **k:pytest.fail('New sealed result takes priority'))
    assert normal.recover(runtime_sha=m.READER_CONSUMER,producer_sha=m.PRODUCER,not_after=m.TARGET,
        out=tmp_path/'one',token='software')['status']=='new_sealed'
    monkeypatch.setattr(normal,'discover',lambda *a,**k:None)
    monkeypatch.setattr(legacy,'recover',lambda **k:calls.append(k))
    assert normal.recover(runtime_sha=m.READER_CONSUMER,producer_sha=m.PRODUCER,not_after=m.TARGET,
        out=tmp_path/'two',token='software',orphan_only=True)['restored'] is False
    assert not calls


def test_actual_normal_fallback_uses_complete_sources_before_644_and_does_not_hide_corruption(case,tmp_path,monkeypatch):
    network(monkeypatch,case)
    monkeypatch.setattr(normal,'discover',lambda *a,**k:None)
    monkeypatch.setattr(legacy,'recover',lambda **k:pytest.fail('644 cannot shadow a complete source pair'))
    first=normal.recover(runtime_sha=m.READER_CONSUMER,producer_sha=m.PRODUCER,not_after=m.TARGET,
        out=tmp_path/'received',token='software')
    assert first['manifest_origin']==m.ORIGIN
    def broken(**kwargs):m.fail('known_corrupt_original')
    monkeypatch.setattr(m,'recover',broken)
    with pytest.raises(m.CompletedPrepareRecoveryError,match='known_corrupt_original'):
        normal.recover(runtime_sha=m.READER_CONSUMER,producer_sha=m.PRODUCER,not_after=m.TARGET,
            out=tmp_path/'second',token='software')


def test_two_cloud_moves_keep_95d_prepared_and_provenance_under_new_m9_seal(case,tmp_path,monkeypatch):
    network(monkeypatch,case); first=recover(tmp_path); args=private_args(tmp_path,monkeypatch,'one')
    normal.admit_resume(first['manifest'],**args)
    for name in ['runtime-activation.json','runtime-activation.intent.json','runtime-activation.restore.json']:
        (args['prepare_out']/name).write_bytes(encode(dict(owner='previous_process',fixture=True,name=name)))
    root=args['consumer'].parent
    plan=root/'scoped-recovery-plan.json';plan.write_bytes(encode(dict(schema='microcap_scoped_recovery_plan_v1',
        consumer_sha=m.READER_CONSUMER,delivery_date=m.TARGET,required_rebalance_dates=[m.TARGET])))
    manifest=root/'scoped-recovery-manifest.json'
    normal.seal(root=root,runtime_sha=m.READER_CONSUMER,producer_sha=m.PRODUCER,plan=plan,out=manifest,
        phase='state',run_id=123456,run_attempt=1,producer_commit='d'*40)
    second=private_args(tmp_path,monkeypatch,'two')
    admitted=normal.admit_resume(manifest,**second)
    assert admitted['prepared_source_reader']['source_auto_sha']==m.SOURCE_HEAD
    assert admitted['prepared_source_reader']['source_run_id']==m.SOURCE_RUN
    assert admitted['prepared_source_reader']['manifest_sha256']==normal.sha_file(manifest)
    assert (second['prepare_out']/'prepared.json').read_bytes()==case['prepared']
    assert json.loads((second['prepare_out']/'prepared.json').read_bytes())['consumer_sha']==m.CONSUMER
    driver.validate_prepared(second['prepare_out']/'prepared.json',second['consumer'],m.TARGET,m.READER_CONSUMER,admitted)
    assert normal.admit_resume(manifest,**second)['admission_reused']
    for name in ['runtime-activation.json','runtime-activation.intent.json','runtime-activation.restore.json']:
        raw=(args['prepare_out']/name).read_bytes()
        assert not (second['prepare_out']/name).exists()
        assert (second['prepare_out']/('prior-runtime-activation-'+digest(raw)+'-'+name)).read_bytes()==raw


@pytest.mark.parametrize('fault',['delete_receipt','other_reader','source_resigned','manifest','target','unclosed_provenance'])
def test_reader_admission_cannot_bypass_original_identity_or_date(case,tmp_path,monkeypatch,fault):
    network(monkeypatch,case); first=recover(tmp_path); args=private_args(tmp_path,monkeypatch)
    admitted=normal.admit_resume(first['manifest'],**args); bad=copy.deepcopy(admitted)
    target=m.TARGET; runtime=m.READER_CONSUMER
    if fault=='delete_receipt':bad.pop('prepared_source_reader')
    elif fault=='other_reader':runtime=m.CONSUMER
    elif fault=='source_resigned':bad['prepared_source_reader']['source_auto_sha']='a'*40
    elif fault=='manifest':bad['manifest_sha256']='0'*64
    elif fault=='target':target='2026-11-13'
    else:bad['objects']=[obj for obj in bad['objects'] if obj['original']!=m.PROVENANCE]
    with pytest.raises(m.CompletedPrepareRecoveryError):
        m.validate_prepared_reader_admission(bad,prepared=args['prepare_out']/'prepared.json',
            config=args['prepare_out']/'consumer-config.json',consumer=args['consumer'],producer=args['producer'],
            target=target,consumer_sha=runtime)


def test_bridge_manifest_cannot_admit_failed_consumer_or_formal_objects(case,tmp_path,monkeypatch):
    network(monkeypatch,case); result=recover(tmp_path); value=json.loads(Path(result['manifest']).read_bytes())
    value['files']['scoped-delivery/stage-receipts/context.json']='a'*64
    value['sizes']['scoped-delivery/stage-receipts/context.json']=1
    value['object_role_counts']=normal.object_role_counts(value['files'])
    with pytest.raises(m.CompletedPrepareRecoveryError,match='manifest_source_only'):
        normal.verify_manifest(value,m.CONSUMER,m.PRODUCER,m.TARGET)


def test_cross_target_admission_is_warm_only_and_never_copies_old_prepared(case,tmp_path,monkeypatch):
    network(monkeypatch,case); result=recover(tmp_path); args=private_args(tmp_path,monkeypatch)
    args['target']='2026-11-13'; report=normal.admit_resume(result['manifest'],**args)
    assert report['same_target'] is False and 'prepared_source_reader' not in report
    assert not (args['prepare_out']/'prepared.json').exists()
    assert not (args['state']/'bootstrap.json').exists()


@pytest.mark.parametrize('variant',['complete','expired','bad_weak_object','later_target'])
def test_weak_new_m9_seal_cannot_hide_completed_original_300_sources(case,tmp_path,monkeypatch,variant):
    _,downloads=network(monkeypatch,case,expired='producer' if variant=='expired' else None)
    objects={'scoped-recovery-plan.json':encode(dict(schema='microcap_scoped_recovery_plan_v1',
        consumer_sha=m.READER_CONSUMER,delivery_date=m.TARGET,required_rebalance_dates=[m.TARGET])),
        'scoped-latest/readback.json':b'weaker original latest readback'}
    weak=dict(schema=normal.SCHEMA,status='resumable_partial',phase='state',runtime_sha=m.READER_CONSUMER,
        producer_sha=m.PRODUCER,producer_commit='d'*40,source_run_id=987654,source_run_attempt=1,
        source_root='/software/weak-source',target=m.TARGET,required_rebalance_dates=[m.TARGET],
        plan_sha256=digest(objects['scoped-recovery-plan.json']),files={n:digest(raw) for n,raw in objects.items()},
        sizes={n:len(raw) for n,raw in objects.items()},formal_state_archive=None,
        native_adoption_required=True,final_strategy_delivery=False,email_sent=False,orders_placed=False)
    if variant=='bad_weak_object':weak['files']['scoped-latest/readback.json']='0'*64
    weak_raw=encode(weak);body=zipped(dict(objects,**{'scoped-recovery-manifest.json':weak_raw}))
    item=dict(id=12345,phase='state',digest='sha256:'+digest(body),size_in_bytes=len(body),
        archive_download_url='https://api.github.com/repos/'+m.REPOSITORY+'/actions/artifacts/12345/zip',
        original_run=dict(id=weak['source_run_id'],run_attempt=1,head_sha=weak['producer_commit']))
    def weak_download(request,path,*,expected_sha,expected_bytes):
        assert digest(body)==expected_sha and len(body)==expected_bytes
        Path(path).write_bytes(body);return dict(sha256=digest(body),bytes=len(body))
    monkeypatch.setattr(normal,'discover',lambda *a,**kw:item)
    monkeypatch.setattr(normal,'stream_download',weak_download)
    target='2026-11-13' if variant=='later_target' else m.TARGET
    kwargs=dict(runtime_sha=m.READER_CONSUMER,producer_sha=m.PRODUCER,not_after=target,
                out=tmp_path/'selected',token='software')
    if variant=='bad_weak_object':
        with pytest.raises(normal.RecoveryError,match='object_hash_differs'):normal.recover(**kwargs)
        assert not downloads
    else:
        selected=normal.recover(**kwargs)
        if variant=='complete':
            assert selected['manifest_origin']==m.ORIGIN and selected['source_run_id']==m.SOURCE_RUN
            prior=selected['prior_weaker_partial']
            assert prior['source_run_id']==weak['source_run_id'] and prior['objects_hash_verified']
            assert Path(prior['outer']).read_bytes()==body and Path(prior['manifest']).read_bytes()==weak_raw
            assert not any(n.startswith('scoped-delivery/') for n in json.loads(Path(selected['manifest']).read_bytes())['files'])
        else:
            assert selected['source_run_id']==weak['source_run_id'] and 'manifest_origin' not in selected
            assert not downloads
