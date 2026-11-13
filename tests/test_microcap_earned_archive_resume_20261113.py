"""P2 preparation: normal seal -> cold admit -> isolated restore -> stage reuse.

Only seal/admit/StageResume/restore_isolated are production implementations.
The tiny native protocol and all prices/manifests are software fixtures. This
does not calculate a strategy or certify real Native market-data gates.

The production route must execute the fresh-child adapter and preserve all
three successful stages. The existing cloud produce/readback workflow also
carries its small observation; it never substitutes for real D12 whole gates.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

DEV = Path(__file__).resolve().parents[1]
OUT = (Path(os.environ['MICROCAP_RECOVERY_ACCEPTANCE_OUT'])/'evidence'
       if os.environ.get('MICROCAP_RECOVERY_ACCEPTANCE_OUT') else
       Path(os.environ['MICROCAP_RCV01_EVIDENCE_OUT'])
       if os.environ.get('MICROCAP_RCV01_EVIDENCE_OUT') else None)
sys.path.insert(0,str(DEV/'scripts'))
import microcap_scoped_whole_recovery as whole
import restore_microcap_rebalance_recovery as recovery
from microcap_scoped_stage_resume import StageResume, formal_objects
from prepare_microcap_local import encode, sha

RUNTIME='906eca8a5dfede7fa1674fb17cd3c6ce12205e6d'
PRODUCER='3344e403ff01e17ef45b745a704a240e0917720b'
AUTO='9033b7619395897212c186115e10fca175b34f61'
TARGET='2026-11-12'

def record(name,value):
    if OUT is None:
        return
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/name).write_bytes(encode(value))

VERSION='''from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parent
CACHE_DIR=ROOT/'.microcap_index_cache'
PRICE_DIR=CACHE_DIR/'prices_raw'
SHARE_DIR=CACHE_DIR/'share_change'
SECURITY_MASTER_CACHE=CACHE_DIR/'security_master.csv'
SHARED_PRICE_DIR=SHARED_SHARE_DIR=SHARED_ADJ_PRICE_DIR=None
PRICE_CACHE_DIR=PRICE_DIR
ADJ_PRICE_CACHE_DIR=CACHE_DIR/'prices_qfq'
SHARE_CACHE_DIR=SHARE_DIR
UNIVERSE_CACHE=CACHE_DIR/'active_universe.csv'
CURRENT_ST_CACHE=CACHE_DIR/'current_st.csv'
SHARED_CACHE_DIR=SHARED_PRICE_CACHE_DIR=SHARED_ADJ_PRICE_CACHE_DIR=SHARED_SHARE_CACHE_DIR=None
def load_security_master():
    return SECURITY_MASTER_CACHE.read_text() if SECURITY_MASTER_CACHE.is_file() else 'cold receiver'
def fetch_share_change():return ROOT
def list_backtest_universe_symbols():
    return sorted(p.stem for p in PRICE_DIR.glob('*.csv') if (SHARE_DIR/p.name).is_file())
freq=SimpleNamespace(**{k:v for k,v in globals().copy().items() if not k.startswith('__')})
base_mod=SimpleNamespace(ROOT=ROOT,CACHE_DIR=CACHE_DIR,freq_mod=freq,fetch_mod=freq)
'''

STATE='''from pathlib import Path
from datetime import date
import microcap_top100_mom16_biweekly_live_v2_0 as v
def validate_state(root,max_anchor_age_days=None,today=None,*,require_current_refresh_proof=True,close_confirmed_session=None):
    assert max_anchor_age_days is None and require_current_refresh_proof is False
    assert today is None or type(today) is date
    assert close_confirmed_session is None or type(close_confirmed_session) is date
    symbols=v.base_mod.freq_mod.list_backtest_universe_symbols()
    master=v.base_mod.freq_mod.load_security_master()
    bad=(Path(root)/'reject-original-state').exists() or symbols!=['000001'] or master!='original staged master'
    return dict(ok=not bad,errors=['original-state-sentinel'] if bad else [],
        opaque={'report_body':['kept',None,False,{'native':'state'}]},
        root=str(Path(root).resolve()),symbols=symbols,master=master,
        arguments=dict(max_anchor_age_days=max_anchor_age_days,today=today.isoformat() if today else None,
            require_current_refresh_proof=require_current_refresh_proof,
            close_confirmed_session=close_confirmed_session.isoformat() if close_confirmed_session else None))
'''

DELIVERY='''from pathlib import Path
import microcap_top100_mom16_biweekly_live_v2_0 as v
def inspect_outputs(root,expected,*,new_generation=False):
    assert type(expected) is str and type(new_generation) is bool
    symbols=v.base_mod.freq_mod.list_backtest_universe_symbols()
    return dict(ok=symbols==['000001'],errors=[] if symbols==['000001'] else ['foreign-root-cached-receiver'],
        expected_date=expected,new_generation=new_generation,root=str(Path(root).resolve()),
        opaque={'report_body':['kept',None,False,{'native':'inspect'}]},symbols=symbols)
'''

NATIVE='''from datetime import date
import json,shutil,sys,tempfile,zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import realtime_state_bundle as state,top100_delivery as delivery
SOURCE_FILES=['microcap_top100_mom16_biweekly_live_v2_0.py','scripts/realtime_state_bundle.py','scripts/top100_delivery.py']
def restore(root,bundle,expected):
    root=Path(root).resolve()
    receiver_before=delivery.inspect_outputs(root,expected)
    with tempfile.TemporaryDirectory(prefix='rcv01-native-stage-') as directory:
        stage=Path(directory)
        for name in SOURCE_FILES:
            target=stage/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(root/name,target)
        with zipfile.ZipFile(bundle) as archive:
            for info in archive.infolist():
                target=stage/info.filename;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(archive.read(info))
        inspected=delivery.inspect_outputs(stage,expected,new_generation=False)
        if inspected['ok'] is not True:raise ValueError('; '.join(inspected['errors']))
        validated=state.validate_state(stage,max_anchor_age_days=None,today=date(2026,11,13),
            require_current_refresh_proof=False,close_confirmed_session=date(2026,11,12))
        if validated['ok'] is not True:raise ValueError('; '.join(validated['errors']))
        with zipfile.ZipFile(bundle) as archive:
            for name in archive.namelist():
                target=root/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(archive.read(name))
        receiver_after=state.validate_state(root,require_current_refresh_proof=False)
        if receiver_after['ok'] is not True:raise ValueError('; '.join(receiver_after['errors']))
        return dict(ok=True,expected_date=expected,restore_source='software_native_restore',
            inspected=inspected,validated=validated,receiver_before=receiver_before,receiver_after=receiver_after)
if __name__=='__main__':
    args=sys.argv[1:]
    report=restore(Path(args[args.index('--root')+1]),Path(args[args.index('--bundle')+1]),args[args.index('--expected-date')+1])
    print(json.dumps(report))
'''

def put(root,name,body):
    path=root/name
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(body if isinstance(body,bytes) else body.encode('utf-8'))
    return path

def native_sources(root):
    for name,body in [('microcap_top100_mom16_biweekly_live_v2_0.py',VERSION),
        ('scripts/realtime_state_bundle.py',STATE),('scripts/top100_delivery.py',DELIVERY),
        ('scripts/top100_cloud_delivery.py',NATIVE)]:
        put(root,name,body)

def argv_for(label,consumer,out):
    native=consumer/'scripts'
    if label=='refresh':return [sys.executable,'-B',native/'top100_delivery.py','refresh-all','--scoped-config',out/'consumer-config.json']
    if label=='check':return [sys.executable,'-B',native/'top100_delivery.py','check']
    return [sys.executable,'-B',native/'top100_cloud_delivery.py','pack','--root',consumer,
        '--v23-root',consumer,'--v25-root',consumer,'--bundle',out/'formal-state.zip','--expected-date',TARGET]

@pytest.fixture
def earned(tmp_path):
    origin=tmp_path/'origin';consumer=origin/'scoped-consumer';out=origin/'scoped-delivery'
    consumer.mkdir(parents=True);out.mkdir()
    native_sources(consumer)
    original_data={
        '.microcap_index_cache/prices_raw/000001.csv':b'date,close_raw\n2026-11-12,100\n',
        '.microcap_index_cache/share_change/000001.csv':b'change_date,total_shares_10k\n2026-11-12,100\n',
        '.microcap_index_cache/security_master.csv':b'original staged master',
        'outputs/nav.csv':b'date,nav\r\n2026-11-12,1\r\n'}
    for name,raw in original_data.items():put(consumer,name,raw)
    m1=encode(dict(ok=True,status='complete',scope='whole_workspace_delivery',expected_date=TARGET,
        release_sha=RUNTIME,verified_at='2026-11-12T17:00:00+00:00',
        inputs={name:sha(raw.replace(b'\r\n',b'\n')) for name,raw in original_data.items() if not name.startswith('outputs/')},
        artifacts={'outputs/nav.csv':sha(original_data['outputs/nav.csv'].replace(b'\r\n',b'\n'))},
        streams={'v2.0':{'latest_date':TARGET},'v2.3':{'latest_date':TARGET},'v2.5':{'latest_date':TARGET}}))
    m2=encode(dict(json.loads(m1),verified_at='2026-11-12T17:01:00+00:00'))
    put(consumer,'outputs/top100_delivery_manifest.json',m1)
    put(out,'formal-manifest-original.json',m1)
    put(out,'consumer-config.json',b'{"software_only":true}\n')
    original_calls=[]
    def native_command(label,argv):
        original_calls.append(label)
        if label=='pack-formal':
            with zipfile.ZipFile(out/'formal-state.zip','w',zipfile.ZIP_DEFLATED) as archive:
                for name,raw in original_data.items():archive.writestr(name,raw)
                archive.writestr('outputs/top100_delivery_manifest.json',m2)
        logs=out/'commands'/label;logs.mkdir(parents=True)
        result={key:logs/name for key,name in [('process','process.json'),('stdout','stdout.log'),('stderr','stderr.log')]}
        result['process'].write_bytes(encode(dict(exit_code=0,command=list(map(str,argv)),completion_unconfirmed=False)))
        result['stdout'].write_bytes(('original-'+label+'\n').encode());result['stderr'].write_bytes(b'')
        return result
    context=dict(schema='microcap_scoped_recovery_transaction_v1',target=TARGET,consumer_sha=RUNTIME,
        source_bundle_sha256='1'*64,parent_checkpoint_sha256='2'*64,scope_sha256='3'*64,
        controller_sources={'frozen-software-controller':'4'*64})
    stages=StageResume(out,consumer,context,native_command)
    stages.call('refresh',argv_for('refresh',consumer,out),lambda:formal_objects(consumer,TARGET))
    stages.call('check',argv_for('check',consumer,out),lambda:formal_objects(consumer,TARGET))
    stages.call('pack-formal',argv_for('pack-formal',consumer,out),[out/'formal-state.zip',out/'formal-manifest-original.json'])
    # A failed publication creates diagnostics, never a successful stage receipt.
    put(out,'commands/publication/process.json',encode(dict(exit_code=1,command=['publish'],completed=True)))
    put(origin,'scoped-recovery-plan.json',encode(dict(schema='microcap_scoped_recovery_plan_v1',consumer_sha=RUNTIME,
        delivery_date=TARGET,required_rebalance_dates=[TARGET])))
    seal=recovery.seal(root=origin,runtime_sha=RUNTIME,producer_sha=PRODUCER,
        plan=origin/'scoped-recovery-plan.json',out=origin/recovery.MANIFESTS['state'],
        phase='state',run_id=43261210223,run_attempt=1,producer_commit=AUTO)
    assert seal['formal_state_archive']['sha256']==sha((out/'formal-state.zip').read_bytes())
    assert not any(name.startswith('scoped-consumer/') for name in seal['files'])
    snapshots={name:(origin/name).read_bytes() for name in seal['files']}
    return dict(origin=origin,consumer=consumer,out=out,seal=seal,context=context,m1=m1,m2=m2,
        original_calls=original_calls,snapshots=snapshots,data=original_data)

def admit(case,tmp_path,monkeypatch,*,repair_oracle=False):
    destination=tmp_path/'cold-receiver'
    consumer=destination/'scoped-consumer';producer=destination/'scoped-source-state/runtime'
    consumer.mkdir(parents=True);producer.mkdir(parents=True)
    native_sources(consumer)
    original_check_output=recovery.subprocess.check_output
    def git_role(argv,**kwargs):
        if argv[-2:]==['rev-parse','HEAD']:
            return (RUNTIME if str(consumer) in argv else PRODUCER)+'\n'
        return original_check_output(argv,**kwargs)
    monkeypatch.setattr(recovery.subprocess,'check_output',git_role)
    commands=[]
    def run(label,argv):
        command=list(map(str,argv));original=list(command)
        if repair_oracle and label=='restore-earned-formal-state':
            # Preparation-only repair oracle; production caller is left unchanged.
            report=destination/'repair-oracle/native-restore.json';report.parent.mkdir()
            command=[sys.executable,'-B','-c',whole.RESTORE,str(consumer),
                str(case['out']/'formal-state.zip'),TARGET,str(report),str(DEV/'scripts')]
        process=subprocess.run(command,cwd=consumer,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),
            capture_output=True,encoding='utf-8',text=True,timeout=30)
        logs=destination/'external-command-evidence'/label;logs.mkdir(parents=True)
        put(logs,'stdout.log',process.stdout);put(logs,'stderr.log',process.stderr)
        value=dict(label=label,original_command=original,executed_command=command,exit_code=process.returncode,
            repair_oracle_substitution=repair_oracle)
        commands.append(value);put(logs,'process.json',encode(value))
        if process.returncode:raise RuntimeError('Native restore rejected: '+process.stderr[-3000:])
        return {key:logs/name for key,name in [('process','process.json'),('stdout','stdout.log'),('stderr','stderr.log')]}
    args=dict(consumer=consumer,producer=producer,state=producer.parent,prepare_out=destination/'scoped-prepare',
        delivery_out=destination/'scoped-delivery',target=TARGET,consumer_sha=RUNTIME,producer_sha=PRODUCER,run=run)
    return args,commands,destination

def reused(case,args):
    forbidden=[]
    def no_repeat(label,argv):
        forbidden.append(label)
        raise AssertionError('Original earned stage must not recompute: '+label)
    stages=StageResume(args['delivery_out'],args['consumer'],case['context'],no_repeat)
    for label in ('refresh','check','pack-formal'):
        outputs=(lambda:formal_objects(args['consumer'],TARGET)) if label!='pack-formal' else [
            args['delivery_out']/'formal-state.zip',args['delivery_out']/'formal-manifest-original.json']
        assert stages.call(label,argv_for(label,args['consumer'],args['delivery_out']),outputs)['stage_reused'] is True
    assert forbidden==[]
    for name,raw in case['snapshots'].items():assert (case['origin']/name).read_bytes()==raw
    assert (args['consumer']/'outputs/top100_delivery_manifest.json').read_bytes()==case['m1']
    assert (args['delivery_out']/'formal-state.zip').read_bytes()==(case['out']/'formal-state.zip').read_bytes()
    sidecar=list(args['delivery_out'].glob('formal-manifest-packed-*.json'))
    assert len(sidecar)==1 and sidecar[0].read_bytes()==case['m2']
    return dict(reused=['refresh','check','pack-formal'],recomputed=[],original_objects_unchanged=True,
        m1_sha256=sha(case['m1']),m2_sha256=sha(case['m2']),formal_archive_sha256=sha((case['out']/'formal-state.zip').read_bytes()))

def test_earned_archive_resume_keeps_native_isolation_and_original_stages(earned,tmp_path,monkeypatch):
    args,commands,destination=admit(earned,tmp_path,monkeypatch)
    result=recovery.admit_resume(earned['origin']/recovery.MANIFESTS['state'],**args)
    assert result['original_formal_manifest_restored'] is True
    reuse=reused(earned,args)
    reports=list(args['delivery_out'].glob('earned-native-restore/*/native-restore.json'))
    assert len(reports)==1
    raw_native=json.loads(reports[0].read_bytes())
    assert raw_native['inspected']['opaque']['report_body'][:3]==['kept',None,False]
    assert raw_native['validated']['opaque']['report_body'][:3]==['kept',None,False]
    assert raw_native['receiver_after']['ok'] is True
    checks=json.loads((reports[0].parent/'native-staged-validation/isolation-receipt.json').read_bytes())
    assert [row['native_function'] for row in checks['original_native_validation_checks']]==['inspect_outputs','validate_state']
    for number,row in enumerate(checks['original_native_validation_checks'],1):
        assert sha((reports[0].parent/f'native-staged-validation/check-{number}/native-report.json').read_bytes())==row['native_report_sha256']
        assert row['native_report_ok'] is True and row['child_exit_code']==0
    value=dict(schema='microcap_earned_archive_isolation_software_v1',
        status='fixed-production-route-accepted',commands=commands,reuse=reuse,
        native_restore_report=raw_native,native_checks=checks,
        adapter_source_sha256=sha((DEV/'scripts/microcap_native_staged_validation.py').read_bytes().replace(b'\r\n',b'\n')),
        native_protocol_fixture_hashes={name:sha(text.encode()) for name,text in (
            ('version',VERSION),('state',STATE),('delivery',DELIVERY),('restore',NATIVE))},
        software_native_protocol=True,formal_native_market_acceptance=False)
    record('small-native-production-route.json',value)
    acceptance=os.environ.get('MICROCAP_RECOVERY_ACCEPTANCE_OUT')
    if acceptance and os.environ.get('MICROCAP_ACCEPTANCE_PHASE')=='produce':
        saved=Path(acceptance)/'state/earned-native-isolation.json'
        saved.parent.mkdir(parents=True,exist_ok=True);saved.write_bytes(encode(value))
    if os.environ.get('MICROCAP_RECOVERY_ACCEPTANCE_RESTORED'):
        previous=json.loads((Path(os.environ['MICROCAP_RECOVERY_ACCEPTANCE_RESTORED'])/'earned-native-isolation.json').read_bytes())
        assert previous['schema']==value['schema']
        assert previous['status']=='fixed-production-route-accepted'
        assert previous['adapter_source_sha256']==value['adapter_source_sha256']
        assert previous['native_protocol_fixture_hashes']==value['native_protocol_fixture_hashes']
        assert previous['reuse']['m1_sha256']==reuse['m1_sha256'] and previous['reuse']['m2_sha256']==reuse['m2_sha256']
        assert previous['reuse']['recomputed']==[]
        record('earned-native-isolation-readback.json',dict(producer_observation_revalidated=True,
            current_fixed_route_executed=True,successful_stages_reused=True,software_native_protocol=True,
            formal_native_market_acceptance=False))

def test_preparation_repair_oracle_preserves_m1_m2_raw_reports_and_stages(earned,tmp_path,monkeypatch):
    args,commands,destination=admit(earned,tmp_path,monkeypatch,repair_oracle=True)
    result=recovery.admit_resume(earned['origin']/recovery.MANIFESTS['state'],**args)
    assert result['formal_archive_restored'] and result['original_formal_manifest_restored']
    evidence=destination/'repair-oracle/native-staged-validation'
    receipt=json.loads((evidence/'isolation-receipt.json').read_bytes())
    assert [row['native_function'] for row in receipt['original_native_validation_checks']]==['inspect_outputs','validate_state']
    reports=[]
    for number,row in enumerate(receipt['original_native_validation_checks'],1):
        path=evidence/f'check-{number}/native-report.json'
        raw=path.read_bytes();report=json.loads(raw)
        assert sha(raw)==row['native_report_sha256'] and report['ok'] is True
        assert report['opaque']['report_body'][:3]==['kept',None,False]
        reports.append(dict(native_function=row['native_function'],report=report,sha256=sha(raw),
            kwargs=row['original_validation_arguments'],child_exit_code=row['child_exit_code']))
    assert reports[0]['kwargs']==dict(expected=TARGET,new_generation=False)
    assert reports[1]['kwargs']==dict(max_anchor_age_days=None,today={'date':'2026-11-13'},
        require_current_refresh_proof=False,close_confirmed_session={'date':TARGET})
    evidence_summary=dict(status='preparation-only-oracle-accepted',commands=commands,
        raw_native_reports=reports,stage_reuse=reused(earned,args),
        source_rewritten=receipt['source_rewritten'],validation_rules_changed=receipt['validation_rules_changed'],
        software_native_protocol=True,production_source_unchanged=True,formal_native_market_acceptance=False)
    record('small-native-isolated-repair-oracle.json',evidence_summary)

def test_old_controller_context_is_not_silently_relabelled(earned):
    path=earned['out']/'stage-receipts/context.json';before=path.read_bytes()
    changed=dict(earned['context'],controller_sources={'frozen-software-controller':'5'*64})
    with pytest.raises(ValueError,match='transaction changed'):
        StageResume(earned['out'],earned['consumer'],changed,lambda *args:pytest.fail('No replay'))
    assert path.read_bytes()==before
