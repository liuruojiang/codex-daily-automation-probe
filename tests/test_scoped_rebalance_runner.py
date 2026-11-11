"""Software orchestration contracts; fixtures are not strategy/data acceptance."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import run_microcap_scoped_rebalance as runner
from prepare_microcap_local import encode,sha


@pytest.fixture
def prepared(tmp_path,monkeypatch):
    consumer=tmp_path/'consumer';consumer.mkdir()
    state=tmp_path/'state';producer=state/'producer';producer.mkdir(parents=True)
    out=tmp_path/'prepared'
    target='2026-10-29';consumer_sha='a'*40
    roster_dir=tmp_path/'weekly';roster_dir.mkdir()
    symbols=[str(n).zfill(6) for n in range(1,301)]
    candidates=encode(dict(candidates=[dict(symbol=s,current_member=i<100) for i,s in enumerate(symbols)]))
    (roster_dir/'candidates.json').write_bytes(candidates)
    pool=tmp_path/'pool.json'
    pool.write_bytes(encode(dict(schema='microcap_local_screen_preparation_v1',status='PREPARATION_VERIFIED',
        date='2026-10-26',run_directory=str(roster_dir),candidates_sha256=sha(candidates))))
    context=dict(runtime_sha='b'*40,target=target)
    manifest=dict(target=target,symbols=symbols,producer_context=context,files={})
    metadata=encode(dict(status='complete',context=context,files={}))
    directory=state/'runs'/target;directory.mkdir(parents=True)
    (directory/'metadata.json').write_bytes(metadata)
    (directory/'result.json').write_bytes(encode(dict(symbols=symbols,scope_sha256=sha(candidates),context=context)))
    calls=[]
    failure={'stage':None}
    stage_fault={'enabled':False}

    def run(label,argv):
        calls.append((label,list(map(str,argv))))
        if label==failure['stage']:raise RuntimeError('software fixture failure: '+label)
        if label=='preflight':
            Path(argv[-1]).write_bytes(encode(dict(target=target,consumer_sha=consumer_sha,release_sha=consumer_sha,
                next_session='2026-10-30',previous_authority=['archive.json','d'*64],rebalance_dates=[target])))
        elif label=='export':Path(argv[-1]).write_bytes(b'software source bundle fixture')
        elif label=='stage':
            receipt=consumer/'.microcap_index_cache/local_input_imports'/('c'*64)/'receipt.json'
            receipt.parent.mkdir(parents=True)
            receipt.write_bytes(encode(dict(status='native_sources_staged',source_bundle_sha256='c'*64,
                files={},production_pointers_changed=stage_fault['enabled'],native_late_adoption_required=True)))
        elif label=='handoff':
            checkpoint=Path(argv[-1])/'checkpoint.json';checkpoint.parent.mkdir(parents=True)
            checkpoint.write_bytes(b'software checkpoint fixture')

    def boundary(out,day):
        folder=out/'boundaries'/day;folder.mkdir(parents=True)
        paths=[folder/'page-01.json',folder/'page-01.receipt.json']
        for path in paths:path.write_bytes(b'software boundary fixture')
        spec=dict(source_root=str(out),directory=folder.relative_to(out).as_posix(),files={p.name:sha(p.read_bytes()) for p in paths})
        path=out/('boundary-'+day+'.json');path.write_bytes(encode(spec))
        return spec,path

    monkeypatch.setattr(runner,'validate_runtime',lambda *a:None)
    monkeypatch.setattr(runner,'build_precision_witness',lambda *a:None)
    monkeypatch.setattr(runner,'completed',lambda *a:True)
    monkeypatch.setattr(runner,'read_bundle',lambda *a:(manifest,{},'c'*64))
    monkeypatch.setattr(runner,'boundary_spec',boundary)
    args=dict(consumer=consumer,producer=producer,state=state,out=out,pool=pool,target=target,consumer_sha=consumer_sha,run=run)
    return args,calls,failure,stage_fault,manifest


def test_prepare_uses_only_verified_300_and_earns_bound_config(prepared):
    args,calls,_,_,_=prepared
    result=runner.prepare(**args)
    assert [c[0] for c in calls]==['preflight','bootstrap','maintain','export','stage','boundary-2026-10-29']
    assert result['issuer_count']==300 and not result['formal_release_verified']
    value,config=runner.validate_prepared(args['out']/'prepared.json',args['consumer'],args['target'],args['consumer_sha'])
    actual=json.loads(config.read_bytes())
    assert actual['source_root']==str(args['producer'])
    assert actual['boundary_snapshots'].keys()=={'2026-10-29'}
    assert not value['email_sent'] and not value['orders_placed']
    assert not any('refresh-all' in item or 'bootstrap-period' in item for _,item in calls)


def test_completed_preparation_reuses_bound_source_package_without_recollection(prepared):
    args,calls,_,_,_=prepared
    first=runner.prepare(**args)
    calls.clear()
    assert runner.prepare(**args)==first
    assert [c[0] for c in calls]==['preflight']


@pytest.mark.parametrize('fault',['unverified_roster','stale_roster','tampered_candidates','incomplete_source','escaped_scope','staging_changed_pointer','preflight_rejected','source_failure'])
def test_prepare_cannot_promote_failed_or_different_inputs(prepared,monkeypatch,fault):
    args,calls,failure,stage_fault,manifest=prepared
    if fault in ('unverified_roster','stale_roster'):
        value=json.loads(args['pool'].read_bytes())
        if fault=='unverified_roster':value['status']='unchecked'
        else:value['date']='2026-10-01'
        args['pool'].write_bytes(encode(value))
    elif fault=='tampered_candidates':
        value=json.loads(args['pool'].read_bytes());(Path(value['run_directory'])/'candidates.json').write_bytes(b'changed')
    elif fault=='incomplete_source':monkeypatch.setattr(runner,'completed',lambda *a:False)
    elif fault=='escaped_scope':manifest['symbols']=manifest['symbols'][:-1]+['999999']
    elif fault=='staging_changed_pointer':stage_fault['enabled']=True
    elif fault=='preflight_rejected':failure['stage']='preflight'
    elif fault=='source_failure':failure['stage']='maintain'
    with pytest.raises((ValueError,RuntimeError)):
        runner.prepare(**args)
    assert not (args['out']/'prepared.json').exists()
    assert not any(label in ('refresh','bootstrap-period','handoff') for label,_ in calls)
    if fault in ('unverified_roster','stale_roster','tampered_candidates','preflight_rejected'):
        assert not any(label=='bootstrap' for label,_ in calls)


def delivery_args(prepared):
    args,calls,*_=prepared
    runner.prepare(**args);calls.clear()
    cp=args['out'].parent/'current-checkpoint.json';cp.write_bytes(b'prior software checkpoint')
    return dict(consumer=args['consumer'],target=args['target'],consumer_sha=args['consumer_sha'],
                prepared=args['out']/'prepared.json',current_checkpoint=cp,out=args['out'].parent/'delivery',run=args['run'])


def test_delivery_requires_both_native_formal_gates_before_period_export(prepared):
    args=delivery_args(prepared)
    value=runner.deliver(**args)
    labels=[label for label,_ in prepared[1]]
    assert labels==['preflight','verify-current','refresh','check','bootstrap-period','handoff','verify-handoff']
    assert value['status']=='native_formal_handoff_verified_not_published'
    assert not value['email_sent'] and not value['orders_placed']
    refresh=next(argv for label,argv in prepared[1] if label=='refresh')
    assert 'refresh-all' in refresh and '--scoped-config' in refresh


def test_bound_precision_witness_is_forwarded_to_native_handoff(prepared,monkeypatch):
    args=delivery_args(prepared)
    witness=args['out'].parent/'bound-witness.json'
    calls=[]
    def build(consumer,current,candidate,out,logs):
        calls.append((current,candidate,logs))
        return witness
    monkeypatch.setattr(runner,'build_precision_witness',build)
    runner.deliver(**args)
    handoff=next(argv for label,argv in prepared[1] if label=='handoff')
    assert handoff[handoff.index('--hedge-precision-witness')+1]==str(witness)
    assert calls[0][0]==args['current_checkpoint']
    assert calls[0][1]==args['out']/'candidate/checkpoint.json'


@pytest.mark.parametrize('stage',['verify-current','refresh','check','bootstrap-period','handoff','verify-handoff'])
def test_failed_delivery_never_gets_a_success_receipt_or_later_stage(prepared,stage):
    args=delivery_args(prepared)
    prepared[2]['stage']=stage
    with pytest.raises(RuntimeError):runner.deliver(**args)
    assert prepared[1][-1][0]==stage
    assert not (args['out']/'result.json').exists()
    if stage in ('verify-current','refresh','check'):
        assert not any(label=='bootstrap-period' for label,_ in prepared[1])


def test_tampered_prepared_config_cannot_reach_refresh(prepared):
    args=delivery_args(prepared)
    (args['prepared'].parent/'consumer-config.json').write_bytes(b'altered consumer config')
    with pytest.raises(ValueError,match='object changed'):runner.deliver(**args)
    assert [label for label,_ in prepared[1]]==['preflight']


@pytest.mark.parametrize('fault',['same_root','nested_producer','outside_state','out_inside_consumer','out_contains_producer'])
def test_paths_cannot_overwrite_a_checkout(prepared,fault):
    args,*_=prepared
    if fault=='same_root':args['producer']=args['consumer']
    elif fault=='nested_producer':args['producer']=args['consumer']/'producer'
    elif fault=='outside_state':args['state']=args['out']/'state'
    elif fault=='out_inside_consumer':args['out']=args['consumer']/'evidence'
    else:args['out']=args['state']
    with pytest.raises(ValueError):runner.prepare(**args)


def test_real_command_failure_keeps_stderr_command_and_exit_code(tmp_path):
    run=runner.Commands(tmp_path)
    with pytest.raises(RuntimeError,match='retained evidence'):
        run('failure',[sys.executable,'-B','-c',"import sys;print('specific blocker',file=sys.stderr);sys.exit(7)"])
    attempt=next((tmp_path/'commands').iterdir())
    assert 'specific blocker' in (attempt/'stderr.log').read_text()
    assert json.loads((attempt/'process.json').read_bytes())['exit_code']==7


def test_missing_dependency_keeps_explicit_unconfirmed_process_evidence(tmp_path):
    run=runner.Commands(tmp_path)
    with pytest.raises(FileNotFoundError):run('missing',[tmp_path/'missing-executable'])
    attempt=next((tmp_path/'commands').iterdir())
    value=json.loads((attempt/'process.json').read_bytes())
    assert value['exit_code'] is None and value['error_type']=='FileNotFoundError'
    assert value['completion_unconfirmed'] is True


def test_boundary_fetch_reuses_original_two_pages_and_matches_native_receipt_contract(tmp_path,monkeypatch):
    import prepare_microcap_local as source
    import requests
    calls=[]
    day='2026-10-29'
    rows=[dict(SECURITY_CODE=str(n).zfill(6),SECUCODE=str(n).zfill(6)+'.SZ',TRADE_DATE=day) for n in range(1,5002)]
    class Response:
        def __init__(self,raw):self.content=raw
        def raise_for_status(self):pass
    def get(url,*,params,timeout,verify):
        calls.append(params['pageNumber'])
        page=params['pageNumber'];part=rows[(page-1)*5000:page*5000]
        return Response(encode(dict(success=True,result=dict(count=5001,pages=2,data=part))))
    monkeypatch.setattr(requests,'get',get)
    spec,path=runner.boundary_spec(tmp_path,day)
    assert calls==[1,2]
    assert spec['files'].keys()=={'page-01.json','page-01.receipt.json','page-02.json','page-02.receipt.json'}
    for page in (1,2):
        folder=tmp_path/spec['directory'];receipt=json.loads((folder/f'page-{page:02d}.receipt.json').read_bytes())
        assert receipt['fetched_at'].endswith('+00:00') and receipt['tls_verify'] is True
        assert receipt['params']==source.request_params(day,page)
        assert receipt['sha256']==sha((folder/f'page-{page:02d}.json').read_bytes())
    assert runner.boundary_spec(tmp_path,day)==(spec,path)
    assert calls==[1,2]


def warm_setup(prepared,monkeypatch,*,fault=False,stale=False):
    import microcap_scoped_settings as settings
    args,calls,*_=prepared
    raw=b'previous close local source cache';digest=sha(raw)
    def download(config,path,target):
        if stale:return dict(status='skipped_stale_optional_local_sources',target='2026-10-01')
        path.write_bytes(raw)
        return dict(status='verified_local_source_bundle_downloaded',target='2026-10-28',sha256=digest,bundle=str(path.resolve()))
    monkeypatch.setattr(settings,'download_sources',download)
    original=args['run']
    def run(label,argv):
        original(label,argv)
        if label=='warm-local-source-inputs':
            directory=args['out']/'commands'/label;directory.mkdir(parents=True)
            report=dict(status='producer_source_inputs_imported',source_target='2026-10-28',bundle_sha256=digest,
                issuer_count=299 if fault else 300,source_preparation_only=True,production_pointers_changed=False,
                native_consumer_gate_required=True,final_strategy_delivery=False,email_sent=False,orders_placed=False,
                producer_pointers_changed=True)
            stdout=directory/'stdout.log';stdout.write_text(json.dumps(report)+'\n')
            process=directory/'process.json';process.write_bytes(encode(dict(command=list(map(str,argv)),exit_code=0)))
            return dict(stdout=stdout,process=process)
    args.update(run=run,warm_source_config=args['out'].parent/'host-config.json')
    return args,calls


def test_local_warm_import_is_between_bootstrap_and_refresh_and_its_evidence_is_bound(prepared,monkeypatch):
    args,calls=warm_setup(prepared,monkeypatch)
    value=runner.prepare(**args)
    assert [label for label,_ in calls][:4]==['preflight','bootstrap','warm-local-source-inputs','maintain']
    warm=value['local_source_warm'];assert warm['producer_import']['status']=='producer_source_inputs_imported'
    assert {'local-source-warm.zip','local-source-download.json',warm['import_receipt']['path']}<=value['files'].keys()
    runner.validate_prepared(args['out']/'prepared.json',args['consumer'],args['target'],args['consumer_sha'])
    (args['out']/'local-source-warm.zip').write_bytes(b'changed earlier source provenance')
    with pytest.raises(ValueError,match='object changed'):
        runner.validate_prepared(args['out']/'prepared.json',args['consumer'],args['target'],args['consumer_sha'])


def test_inconsistent_producer_import_receipt_cannot_reach_normal_maintenance(prepared,monkeypatch):
    args,calls=warm_setup(prepared,monkeypatch,fault=True)
    with pytest.raises(ValueError,match='import result differs'):runner.prepare(**args)
    assert not any(label=='maintain' for label,_ in calls)
    assert not (args['out']/'prepared.json').exists()


def test_optional_stale_local_sources_are_explicitly_skipped_before_normal_refresh(prepared,monkeypatch):
    args,calls=warm_setup(prepared,monkeypatch,stale=True)
    value=runner.prepare(**args)
    assert not any(label=='warm-local-source-inputs' for label,_ in calls)
    assert value['local_source_warm']['status']=='skipped_stale_optional_local_sources'
    assert 'local-source-download.json' in value['files']
