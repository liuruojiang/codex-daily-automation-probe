"""Independent notice-prefix transport/admission attacks, no market requests.

The reused fixture patches the approved profile to explicit software objects.
Only separate actual P2/export/cloud evidence may establish the real prefix.
"""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import microcap_notice_prefix as m
from prepare_microcap_local import encode
from test_microcap_notice_prefix import notice_case, put

REAL_ROLE=m.runtime_role


def rewrite(c, name, mutate):
    with zipfile.ZipFile(c['bundle']) as archive:
        objects={n:archive.read(n) for n in archive.namelist()}
    manifest=json.loads(objects['manifest.json'])
    mutate(manifest,objects)
    objects['manifest.json']=encode(manifest)
    bad=c['bundle'].with_name(name+'.zip')
    with zipfile.ZipFile(bad,'w',zipfile.ZIP_DEFLATED) as archive:
        for path,raw in objects.items():archive.writestr(path,raw)
    return bad


def unchanged(c):
    return {p:p.read_bytes() for p in [c['root']/m.POINTER,c['root']/m.FRESHNESS,*c['refs']]}


def test_explicit_D12_intake_retains_both_original_D11_proof_clocks_and_never_claims_D12_freshness(notice_case):
    c=notice_case;before=unchanged(c)
    result=m.import_prefix(**c['args'])
    active=(c['root']/m.FRESHNESS).read_bytes()
    assert result['requested_not_after']=='2026-11-12' and result['target']=='2026-11-11'
    assert result['source_inputs_only'] is True and result['current_signal_ready'] is False
    assert result['final_strategy_delivery'] is False
    assert active==c['bodies'][m.RECEIPTS[1]['path']]
    for spec in m.RECEIPTS:
        assert (c['root']/spec['path']).read_bytes()==c['bodies'][spec['path']]
    proof=json.loads(active)
    assert proof['target']=='2026-11-11' and proof['completed_at']=='2026-11-12T02:58:00+00:00'
    assert all(path.read_bytes()==raw for path,raw in before.items() if path not in (c['root']/m.POINTER,c['root']/m.FRESHNESS))


@pytest.mark.parametrize('fault',['clock_promise','case_global_freshness','missing_first_receipt','endpoint_pointer_alias'])
def test_inspector_rejects_resealed_profile_contract_or_mutable_object_alias(notice_case,fault):
    c=notice_case
    def mutate(value,objects):
        if fault=='clock_promise':value['original_clocks_retained']=False
        elif fault=='case_global_freshness':
            name=m.PREFIX+'FRESHNESS.json';raw=b'{}'
            objects[name]=raw;value['files'][name]=hashlib.sha256(raw).hexdigest();value['sizes'][name]=len(raw)
        elif fault=='missing_first_receipt':
            name=m.RECEIPTS[0]['path'];objects.pop(name);value['files'].pop(name);value['sizes'].pop(name)
        else:value['end_pointer_spec']['path']=m.PREFIX+'different/previous_pointer.json'
    bad=rewrite(c,fault,mutate)
    with pytest.raises(m.NoticePrefixError):
        m.inspect_bundle(bundle=bad,expected_sha=m.sha_file(bad),not_after='2026-11-12')


def test_coherently_rehashed_omitted_prefix_object_still_requires_original_native_closure(notice_case):
    c=notice_case;old=unchanged(c)
    def remove(value,objects):
        path=m.PREFIX+'original-http/page.json'
        objects.pop(path);value['files'].pop(path);value['sizes'].pop(path)
        value.get('payload_files',{}).pop(path,None)
        value.get('baseline_files',{}).pop(path,None)
    bad=rewrite(c,'omitted-original-body',remove)
    with pytest.raises((m.NoticePrefixError,FileNotFoundError)):
        m.import_prefix(**dict(c['args'],bundle=bad,expected_sha=m.sha_file(bad)))
    assert all(path.read_bytes()==raw for path,raw in old.items())


@pytest.mark.parametrize('wrong_role',['producer','protected','executable'])
def test_actual_git_role_gate_cannot_be_disabled_by_a_valid_prefix_package(notice_case,monkeypatch,wrong_role):
    c=notice_case;old=unchanged(c);c['calls'].clear()
    monkeypatch.setattr(m,'runtime_role',REAL_ROLE)
    def git(command,**kwargs):
        root=Path(command[2]).resolve()
        if command[3:]==['rev-parse','HEAD']:
            pin=m.PRODUCER if root==c['root'] else m.CONSUMER
            if wrong_role=='producer' and root==c['root']:pin=m.CONSUMER
            if wrong_role=='protected' and root==c['protected']:pin=m.PRODUCER
            return pin+'\n'
        assert command[3:]==['diff','--name-only','HEAD']
        return 'scripts/st_notice_refresh.py\n' if wrong_role=='executable' and root==c['root'] else ''
    monkeypatch.setattr(m.subprocess,'check_output',git)
    with pytest.raises(m.NoticePrefixError,match='git_role|executable'):
        m.import_prefix(**c['args'])
    assert c['calls']==[] and all(path.read_bytes()==raw for path,raw in old.items())


def test_protected_consumer_cannot_be_used_as_the_source_writer_role(notice_case):
    c=notice_case;old=unchanged(c);c['calls'].clear()
    with pytest.raises(m.NoticePrefixError,match='private_separate_roles'):
        m.import_prefix(**dict(c['args'],root=c['protected']))
    assert c['calls']==[] and all(path.read_bytes()==raw for path,raw in old.items())


def test_real_newer_private_pair_remains_byte_identical_in_covered_path(notice_case):
    c=notice_case
    newer=[m.PREFIX+'newer/archive.json','f'*64]
    put(c['root'],m.POINTER,dict(manifest_path=newer[0],manifest_sha256=newer[1]))
    put(c['root'],m.FRESHNESS,dict(target='2026-11-12',authority=newer,completed_at='2026-11-12T09:00:00+00:00'))
    original=unchanged(c);native=c['native']
    def verified_newer(runtime,data,mode,profile_root=None):
        if mode=='prefix':return native(runtime,data,mode,profile_root)
        assert json.loads((Path(data)/m.POINTER).read_bytes())['manifest_path']==newer[0]
        return dict(mode='current',state='covered',authority=newer,native_runtime=m.PRODUCER,
            old_prefix_unchanged=True,original_clocks_retained=True)
    result=m.import_prefix(**dict(c['args'],native=verified_newer))
    assert result['status']=='skipped_notice_prefix_already_covered' and result['authority']==newer
    assert result['private_pointer_changed'] is result['private_freshness_changed'] is False
    assert all(path.read_bytes()==raw for path,raw in original.items())


def test_unknown_freshness_owner_mid_commit_is_preserved_without_success_receipt(notice_case,monkeypatch):
    c=notice_case;writer=m._write_pair_member
    foreign=b'explicit software foreign freshness owner\n'
    def changed(path,raw):
        writer(path,raw)
        if Path(path)==c['root']/m.POINTER:
            (c['root']/m.FRESHNESS).write_bytes(foreign)
    monkeypatch.setattr(m,'_write_pair_member',changed)
    with pytest.raises(m.NoticePrefixError,match='rollback_owner_unknown'):
        m.import_prefix(**c['args'])
    assert (c['root']/m.FRESHNESS).read_bytes()==foreign
    assert not (c['args']['out']/'receipt.json').exists()
    assert json.loads((c['args']['out']/'transaction.json').read_bytes())['status']=='intent'
    assert all(path.read_bytes()==raw for path,raw in c['refs'].items())


def test_lost_success_response_is_reconciled_without_rewriting_source_pair(notice_case,monkeypatch):
    c=notice_case;m.import_prefix(**c['args']);pair=m._pair(c['root'])
    journal=c['args']['out']/'transaction.json';value=json.loads(journal.read_bytes());value['status']='intent';journal.write_bytes(encode(value))
    monkeypatch.setattr(m,'_write_pair_member',lambda *a:pytest.fail('Already committed pair must not be rewritten'))
    result=m.import_prefix(**c['args'])
    assert result['status']=='skipped_notice_prefix_already_covered' and m._pair(c['root'])==pair
    assert json.loads(journal.read_bytes())['status'] in ('complete','reconciled')


def test_release_issuer_mismatch_refuses_cached_download_before_any_adoption(notice_case,tmp_path,monkeypatch):
    c=notice_case;old=unchanged(c);c['calls'].clear()
    out=tmp_path/'warm';out.mkdir();shutil.copyfile(c['bundle'],out/'notice-prefix.zip')
    config=tmp_path/'notice-config.json'
    config.write_bytes(encode(dict(schema=m.POINTER_SCHEMA,owner='local',notice_prefix=dict(
        url='https://github.com/liuruojiang/codex-daily-automation-probe/releases/download/microcap-notice-prefix-software/prefix.zip',
        sha256=m.sha_file(c['bundle']),target=m.TARGET,runtime_sha=m.PRODUCER,producer_commit='e'*40))))
    monkeypatch.setattr(m,'stream_download',lambda *a,**k:pytest.fail('Verified cached bundle must not redownload'))
    with pytest.raises(m.NoticePrefixError,match='release_issuer_differs'):
        m.warm(config=config,root=c['root'],protected_root=c['protected'],state=c['args']['state'],
            pool=c['args']['pool'],target='2026-11-12',out=out,native=c['native'])
    assert c['calls']==[] and all(path.read_bytes()==raw for path,raw in old.items())


@pytest.fixture
def prepare_pipeline(notice_case,tmp_path,monkeypatch):
    """Actual Auto prepare+warm, software Native maintenance/export envelope."""
    import run_microcap_scoped_rebalance as runner
    # Keep Windows' test-only envelope below the host's legacy path limit.
    c=notice_case;target='2026-11-12';out=tmp_path.parent/('p-'+hashlib.sha256(tmp_path.name.encode()).hexdigest()[:8])
    symbols=[str(n).zfill(6) for n in range(300)]
    roster=tmp_path/'weekly';roster.mkdir()
    candidates=encode(dict(candidates=[dict(symbol=s,current_member=i<100) for i,s in enumerate(symbols)]))
    put(roster,'candidates.json',candidates)
    pool=put(tmp_path,'pipeline-pool.json',dict(schema='microcap_local_screen_preparation_v1',
        status='PREPARATION_VERIFIED',date='2026-11-09',run_directory=str(roster),
        candidates_sha256=hashlib.sha256(candidates).hexdigest()))
    config=put(tmp_path,'pipeline-notice-config.json',dict(schema=m.POINTER_SCHEMA,owner='local',notice_prefix=dict(
        url='https://github.com/liuruojiang/codex-daily-automation-probe/releases/download/microcap-notice-prefix-software/prefix.zip',
        sha256=m.sha_file(c['bundle']),target=m.TARGET,runtime_sha=m.PRODUCER,producer_commit='d'*40)))
    context=dict(runtime_sha=m.PRODUCER,target=target)
    directory=c['args']['state']/'runs'/target
    put(directory,'metadata.json',dict(status='complete',context=context,files={}))
    put(directory,'result.json',dict(symbols=symbols,scope_sha256=hashlib.sha256(candidates).hexdigest(),context=context))
    source_manifest=dict(target=target,symbols=symbols,producer_context=context,files={})
    monkeypatch.setattr(runner,'validate_runtime',lambda *a:None)
    monkeypatch.setattr(runner,'build_precision_witness',lambda *a:None)
    monkeypatch.setattr(runner,'completed',lambda *a:True)
    monkeypatch.setattr(runner,'read_bundle',lambda *a:(source_manifest,{},'c'*64))
    def boundary(folder,day):
        stage=folder/'boundaries'/day;stage.mkdir(parents=True,exist_ok=True)
        page=put(stage,'software-boundary.json',b'explicit software dated boundary\n')
        value=dict(source_root=str(folder),directory=stage.relative_to(folder).as_posix(),files={page.name:hashlib.sha256(page.read_bytes()).hexdigest()})
        path=put(folder,'boundary-'+day+'.json',value)
        return value,path
    monkeypatch.setattr(runner,'boundary_spec',boundary)
    calls=[];fail={'maintain':False}
    def run(label,argv):
        argv=list(map(str,argv));calls.append((label,argv))
        if label=='preflight':
            put(Path(argv[-1]).parent,Path(argv[-1]).name,dict(target=target,consumer_sha=m.CONSUMER,
                release_sha=m.CONSUMER,next_session='2026-11-13',previous_authority=m.START,rebalance_dates=[target]))
        elif label=='warm-approved-notice-prefix':
            selected=Path(argv[argv.index('--out')+1]);selected.mkdir(parents=True,exist_ok=True)
            bundle=selected/'notice-prefix.zip'
            if not bundle.exists():shutil.copyfile(c['bundle'],bundle)
            result=m.warm(config=config,root=c['root'],protected_root=c['protected'],state=c['args']['state'],
                pool=pool,target=target,out=selected,native=c['native'])
            command=out/'commands'/(label+'-'+str(len(calls)))
            stdout=put(command,'stdout.log',(json.dumps(result)+'\n').encode())
            process=put(command,'process.json',dict(exit_code=0,command=argv,explicit_software_process_envelope=True))
            return dict(stdout=str(stdout),process=str(process))
        elif label=='select-original-notice-forward-checkpoint':
            def forward(runtime,data,mode,profile_root=None,requested_target=None):
                assert mode=='forward'
                raw=(Path(data)/m.FRESHNESS).read_bytes();active=json.loads(raw)
                value=dict(mode='forward',native_runtime=m.PRODUCER,authority=m.END,authority_day=m.TARGET,
                    requested_target=requested_target,active_sha256=hashlib.sha256(raw).hexdigest(),
                    old_prefix_unchanged=True,original_clocks_retained=True,state='noop',reason='already_advancing',
                    selected_spec=m.RECEIPTS[0])
                if requested_target>m.TARGET and active['previous_authority']==m.END:
                    value.update(state='select',active_spec=m.RECEIPTS[1],selected_previous_authority=m.START,
                        source_files={spec['path']:spec['sha256'] for spec in m.RECEIPTS})
                return value
            selected=Path(argv[argv.index('--out')+1])
            result=m.select_forward(root=c['root'],protected_root=c['protected'],state=c['args']['state'],
                pool=pool,target=target,out=selected,native=forward)
            command=out/'commands'/(label+'-'+str(len(calls)))
            stdout=put(command,'stdout.log',(json.dumps(result)+'\n').encode())
            process=put(command,'process.json',dict(exit_code=0,command=argv,explicit_software_process_envelope=True))
            return dict(stdout=str(stdout),process=str(process))
        elif label=='maintain':
            assert argv[argv.index('--as-of')+1]==target
            assert json.loads((c['root']/m.FRESHNESS).read_bytes())['target']=='2026-11-11'
            if fail['maintain']:raise RuntimeError('explicit software D12 maintain failure')
        elif label=='export':put(Path(argv[-1]).parent,Path(argv[-1]).name,b'explicit software source envelope')
        elif label=='stage':
            put(c['protected'],'.microcap_index_cache/local_input_imports/'+'c'*64+'/receipt.json',dict(
                status='native_sources_staged',source_bundle_sha256='c'*64,files={},
                production_pointers_changed=False,native_late_adoption_required=True))
    args=dict(consumer=c['protected'],producer=c['root'],state=c['args']['state'],out=out,pool=pool,
        target=target,consumer_sha=m.CONSUMER,run=run,warm_notice_prefix_config=config)
    return c,runner,args,calls,fail


def test_real_prepare_warms_notice_without_any_stock_warm_then_still_runs_D12_maintenance(prepare_pipeline):
    c,runner,args,calls,_=prepare_pipeline
    result=runner.prepare(**args)
    labels=[label for label,_ in calls]
    assert labels.index('bootstrap')<labels.index('warm-approved-notice-prefix')<labels.index('maintain')
    assert 'warm-local-source-inputs' not in labels
    assert result['target']=='2026-11-12' and result['status']=='prepared_sources_only'
    assert result['formal_release_verified'] is False and result['email_sent'] is False
    notice_receipts=[name for name in result['files'] if name.startswith('receipts/notice-prefix-')]
    assert len(notice_receipts)==1
    warm=json.loads((args['out']/notice_receipts[0]).read_bytes())
    assert warm['target']=='2026-11-11' and warm['fresh_for_requested_date'] is False
    assert warm['requested_not_after']=='2026-11-12'


def test_notice_prefix_success_does_not_mark_a_failed_D12_maintenance_prepared(prepare_pipeline):
    c,runner,args,calls,fail=prepare_pipeline
    fail['maintain']=True
    with pytest.raises(RuntimeError,match='D12 maintain failure'):runner.prepare(**args)
    assert not (args['out']/'prepared.json').exists()
    before=m._pair(c['root'])
    assert json.loads(before[m.FRESHNESS])['target']=='2026-11-11'
    calls.clear();fail['maintain']=False
    result=runner.prepare(**args)
    assert m._pair(c['root'])==before and result['target']=='2026-11-12'
    assert [label for label,_ in calls].count('maintain')==1
    assert [label for label,_ in calls].count('warm-approved-notice-prefix')==1


def test_restored_source_admission_still_reaches_independent_notice_warm(prepare_pipeline,tmp_path,monkeypatch):
    c,runner,args,calls,_=prepare_pipeline
    def admitted(*values):calls.append(('prior-source-admitted',['explicit software source admission boundary']))
    monkeypatch.setattr(runner,'admit_sources',admitted)
    marker=put(tmp_path,'prior-source-manifest.json',dict(explicit_software_admission=True))
    result=runner.prepare(**dict(args,resume_state=marker))
    labels=[label for label,_ in calls]
    assert labels.index('prior-source-admitted')<labels.index('warm-approved-notice-prefix')<labels.index('maintain')
    assert result['target']=='2026-11-12' and result['formal_release_verified'] is False


def test_saved_prepared_keeps_original_closure_when_notice_warm_becomes_covered(prepare_pipeline):
    c,runner,args,calls,_=prepare_pipeline
    first=runner.prepare(**args);saved=(args['out']/'prepared.json').read_bytes()
    pair=m._pair(c['root']);calls.clear()
    again=runner.prepare(**args)
    assert again==first and (args['out']/'prepared.json').read_bytes()==saved
    assert [label for label,_ in calls]==['preflight','warm-approved-notice-prefix','select-original-notice-forward-checkpoint']
    assert m._pair(c['root'])==pair
    value,_=runner.validate_prepared(args['out']/'prepared.json',args['consumer'],args['target'],args['consumer_sha'])
    assert value==first


@pytest.fixture
def borrowed_notice_case(notice_case):
    """Tiny explicit old-closure partition, no real 1.94GB copy or claim."""
    c=notice_case;original=c['native']
    borrowed=m.PREFIX+'seed.dat';proof_needed=m.PREFIX+'needed.dat'
    for path,raw in ((borrowed,b'old approved baseline bytes\n'),(proof_needed,b'old bytes also required by new proof\n')):
        c['bodies'][path]=raw;c['files'][path]=hashlib.sha256(raw).hexdigest()
        put(c['origin'],path,raw);put(c['root'],path,raw)
    measured_old={path:c['files'][path] for path in (borrowed,proof_needed)}
    measured_proof={proof_needed:c['files'][proof_needed],
        m.PREFIX+'original-http/page.json':c['files'][m.PREFIX+'original-http/page.json']}
    views=[]
    def native(runtime,data,mode,profile_root=None):
        result=original(runtime,data,mode,profile_root)
        if mode=='prefix':
            result=dict(result,sizes={n:len(raw) for n,raw in c['bodies'].items()},
                baseline_files=measured_old,proof_files=measured_proof)
            if Path(data)!=c['origin']:
                assert (Path(data)/borrowed).samefile(c['root']/borrowed)
                views.append(Path(data))
        return result
    bundle=c['bundle'].with_name('explicit-baseline-delta.zip')
    m.export_prefix(root=c['origin'],bundle=bundle,issuer_head='d'*40,native=native)
    c.update(bundle=bundle,native=native,borrowed=borrowed,proof_needed=proof_needed,views=views)
    c['args'].update(bundle=bundle,expected_sha=m.sha_file(bundle),native=native)
    return c


def test_payload_excludes_only_native_measured_old_baseline_and_view_links_it_read_only(borrowed_notice_case):
    c=borrowed_notice_case
    value=m.inspect_bundle(bundle=c['bundle'],expected_sha=m.sha_file(c['bundle']),not_after='2026-11-12')
    assert value['baseline_files']=={c['borrowed']:c['files'][c['borrowed']]}
    assert c['proof_needed'] in value['payload_files']
    with zipfile.ZipFile(c['bundle']) as archive:
        assert c['borrowed'] not in archive.namelist() and c['proof_needed'] in archive.namelist()
    source=c['root']/c['borrowed'];before=(source.read_bytes(),source.stat().st_nlink)
    result=m.import_prefix(**c['args'])
    assert result['status']=='approved_notice_prefix_imported_source_only'
    assert c['views'] and all(not path.exists() for path in c['views'])
    assert (source.read_bytes(),source.stat().st_nlink)==before


@pytest.mark.parametrize('fault',['missing','changed'])
def test_missing_or_changed_borrowed_baseline_cannot_be_replaced_from_the_sidecar(borrowed_notice_case,fault):
    c=borrowed_notice_case;old=m._pair(c['root']);source=c['root']/c['borrowed']
    if fault=='missing':source.unlink()
    else:source.write_bytes(b'foreign baseline bytes\n')
    with pytest.raises(m.NoticePrefixError,match='baseline_object_missing_or_changed'):
        m.import_prefix(**c['args'])
    assert m._pair(c['root'])==old and not (c['args']['out']/'receipt.json').exists()
    assert (not source.exists()) if fault=='missing' else source.read_bytes()==b'foreign baseline bytes\n'


@pytest.mark.parametrize('fault',['cross_partition','changed_partition_digest'])
def test_same_path_partition_conflict_is_rejected_before_any_private_authority_change(borrowed_notice_case,fault):
    c=borrowed_notice_case;old=m._pair(c['root'])
    def mutate(value,objects):
        path=c['borrowed']
        if fault=='cross_partition':value['payload_files'][path]=value['baseline_files'][path]
        else:value['baseline_files'][path]='a'*64
    bad=rewrite(c,fault,mutate)
    with pytest.raises(m.NoticePrefixError,match='partition'):
        m.import_prefix(**dict(c['args'],bundle=bad,expected_sha=m.sha_file(bad)))
    assert m._pair(c['root'])==old


@pytest.mark.parametrize('path_kind',['not_in_old_closure','old_but_required_by_proof'])
def test_local_matching_bytes_cannot_authorize_borrowing_a_native_required_payload_dependency(borrowed_notice_case,path_kind):
    c=borrowed_notice_case;old=m._pair(c['root'])
    path=m.PREFIX+'original-http/page.json' if path_kind=='not_in_old_closure' else c['proof_needed']
    put(c['root'],path,c['bodies'][path])
    def mutate(value,objects):
        value['baseline_files'][path]=value['payload_files'].pop(path)
        objects.pop(path)
    bad=rewrite(c,path_kind,mutate)
    # Transport is coherent, and local bytes even match: Native's independently
    # measured old/proof partition must still refuse the unauthorized borrowing.
    m.inspect_bundle(bundle=bad,expected_sha=m.sha_file(bad),not_after='2026-11-12')
    with pytest.raises(m.NoticePrefixError,match='native_and_transport_closure_differ'):
        m.import_prefix(**dict(c['args'],bundle=bad,expected_sha=m.sha_file(bad)))
    assert m._pair(c['root'])==old and not (c['args']['out']/'receipt.json').exists()


def test_saved_prepared_reuse_still_borrows_exact_old_baseline_without_recollection(
        borrowed_notice_case,prepare_pipeline):
    c,runner,args,calls,_=prepare_pipeline
    assert c is borrowed_notice_case
    source=c['root']/c['borrowed'];before=(source.read_bytes(),source.stat().st_nlink)
    first=runner.prepare(**args);saved=(args['out']/'prepared.json').read_bytes()
    calls.clear();again=runner.prepare(**args)
    assert again==first and (args['out']/'prepared.json').read_bytes()==saved
    assert [name for name,_ in calls]==['preflight','warm-approved-notice-prefix','select-original-notice-forward-checkpoint']
    assert (source.read_bytes(),source.stat().st_nlink)==before
    assert all(not view.exists() for view in c['views'])
