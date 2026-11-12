"""Prepare scoped close sources, then separately earn a formal member handoff.

Requires an explicit verified weekly roster and restored independent checkouts.
No publication, SMTP, orders, runtime activation or full-universe collection.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

from prepare_microcap_local import atomic, encode, fetch_snapshot, sha, writer_lock
from maintain_microcap_local import load_pool, validate_runtime, hashes
from microcap_scoped_inputs import read_bundle
from microcap_local_due import completed
from microcap_handoff_witness import build as build_precision_witness

SCRIPTS = Path(__file__).resolve().parent

# Each native call runs in its own interpreter: producer/native script modules
# from different exact Git checkouts must never share Python's import cache.
PREFLIGHT = r'''
import json,sys,subprocess
from pathlib import Path
from datetime import date
root=Path(sys.argv[1]).resolve();sys.path.insert(0,str(root))
from scripts.top100_delivery import verify_release
from scripts.exchange_calendar import latest_completed_session
from scripts.member_period_handoff import schedule
from scripts.st_history_scope import resolve_archive_reference
import pandas as pd
import microcap_top100_mom16_biweekly_live_v2_0 as v2
target=sys.argv[2];actual=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
if actual!=sys.argv[3]:raise ValueError('Consumer checkout differs from exact requested runtime')
release=verify_release(root)
if latest_completed_session().isoformat()!=target:raise ValueError('Rebalance target is not the latest completed exchange close')
s=schedule(root,target)
if not s['is_trading_day']:raise ValueError('Not an original native trading session')
recovery=None
if len(sys.argv)>5:
    from scripts.member_period_handoff import verify,read_bound,digest
    recovery_path=Path(sys.argv[5]).resolve()
    recovery=json.loads(recovery_path.read_bytes())
    if (recovery.get('schema')!='microcap_scoped_recovery_plan_v1'
            or recovery.get('target')!=target or recovery.get('delivery_date')!=target
            or recovery.get('consumer_sha')!=actual or recovery.get('run') is not True):
        raise ValueError('Recovery plan differs from the real completed session/runtime')
    cp_path=Path(recovery['latest_checkpoint_path']).resolve()
    if digest(cp_path.read_bytes())!=recovery['latest_checkpoint_sha256']:
        raise ValueError('Recovery planning checkpoint changed')
    cp=verify(cp_path);period=json.loads(read_bound(cp,'period'))
    from scripts.exchange_calendar import sessions_for_day
    from microcap_top100_mom16_biweekly_live_v2_0 import base_mod
    calendar=pd.DatetimeIndex(sessions_for_day(date.fromisoformat(target))).normalize()
    missing=[str(d.date()) for d in base_mod.build_biweekly_rebalance_dates(calendar)
             if pd.Timestamp(period['anchor_day'])<d<=pd.Timestamp(target)]
    if missing!=recovery.get('required_rebalance_dates'):
        raise ValueError('Recovery plan omits or invents an original rebalance')
    if (cp['as_of_date']>target or recovery.get('latest_checkpoint_as_of_date')!=cp['as_of_date']
            or recovery.get('latest_checkpoint_period_status')!=cp.get('period_status','current')):
        raise ValueError('Recovery plan checkpoint date/phase differs')
    if not missing and not (recovery.get('action')=='daily_catchup'
                           and recovery.get('needs_checkpoint_advance') is True
                           and cp['as_of_date']<target and cp.get('period_status','current')=='current'):
        raise ValueError('Recovery has no uncompleted rebalance or confirmed checkpoint gap')
    if missing:
        earlier=calendar[calendar<pd.Timestamp(missing[0])]
        if (not len(earlier) or recovery.get('parent_not_after_date')!=str(earlier[-1].date())
                or recovery.get('recovery_R')!=missing[0] or recovery.get('last_required_R')!=missing[-1]):
            raise ValueError('Recovery plan changed an original rebalance/parent clock')
elif not s['is_rebalance']:
    raise ValueError('Not an original native rebalance session')
m=json.loads((root/'outputs/top100_delivery_manifest.json').read_bytes())
if m.get('ok') is not True or m.get('status')!='complete' or m.get('scope')!='whole_workspace_delivery':
    raise ValueError('Restored consumer has no completed formal baseline manifest')
bridge=v2.base_mod.read_csv_last_date(root/'outputs/wind_microcap_top_100_biweekly_thursday_16y_cached.csv')
from scripts.exchange_calendar import sessions_for_day
from datetime import date
sessions=pd.DatetimeIndex(sessions_for_day(date.fromisoformat(target))).normalize()
dates=v2.base_mod.build_biweekly_rebalance_dates(sessions)
boundaries=[str(d.date()) for d in dates if pd.Timestamp(bridge)<d<=pd.Timestamp(target)]
result=dict(target=target,next_session=s['next_session'],consumer_sha=actual,release_sha=release,
            previous_authority=list(resolve_archive_reference(root)),rebalance_dates=boundaries)
if recovery is not None:
    result['recovery_plan_sha256']=digest(recovery_path.read_bytes())
    result['recovery_rebalance_dates']=missing
Path(sys.argv[4]).write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
'''

BOUNDARY_READBACK = r'''
import sys,json
from pathlib import Path
root=Path(sys.argv[1]).resolve();sys.path.insert(0,str(root))
from scripts.scoped_cap_boundary import read_snapshot
spec=json.loads(Path(sys.argv[3]).read_bytes())
rows,files=read_snapshot(Path(spec['source_root']),spec,sys.argv[2])
print(json.dumps(dict(date=sys.argv[2],rows=len(rows),source_files=files)))
'''


class Commands:
    def __init__(self, out):
        self.out = Path(out)
        self.logs = self.out / 'commands'
        self.logs.mkdir(parents=True, exist_ok=True)

    def __call__(self, label, argv):
        attempt = self.logs / (label + '-' + uuid.uuid4().hex)
        attempt.mkdir()
        record = dict(command=[str(a) for a in argv], started_at=datetime.now(timezone.utc).isoformat())
        environment = os.environ.copy()
        environment['PYTHONDONTWRITEBYTECODE'] = '1'
        try:
            with (attempt/'stdout.log').open('w',encoding='utf-8') as stdout, (attempt/'stderr.log').open('w',encoding='utf-8') as stderr:
                process = subprocess.run(record['command'], stdout=stdout, stderr=stderr, env=environment)
        except BaseException as exc:
            record.update(exit_code=None,completed_at=datetime.now(timezone.utc).isoformat(),
                          error_type=type(exc).__name__,error=str(exc),completion_unconfirmed=True)
            atomic(attempt/'process.json',encode(record))
            raise
        record.update(exit_code=process.returncode, completed_at=datetime.now(timezone.utc).isoformat())
        atomic(attempt/'process.json',encode(record))
        if process.returncode:
            raise RuntimeError(f'Scoped rebalance stage {label} failed; retained evidence: {attempt}')
        return dict(stdout=attempt/'stdout.log',stderr=attempt/'stderr.log',process=attempt/'process.json')


def save_once(path, raw):
    path = Path(path)
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError('Preserved preparation object differs; use a new output directory: ' + str(path))
    else:
        atomic(path, raw)


def validate_paths(consumer, producer, state, out):
    consumer, producer, state, out = map(lambda p: Path(p).resolve(), (consumer, producer, state, out))
    if consumer == producer or consumer.is_relative_to(producer) or producer.is_relative_to(consumer):
        raise ValueError('Consumer and producer must be independent checkouts')
    if producer == state or not producer.is_relative_to(state):
        raise ValueError('Producer must live below its dedicated state root')
    if state == consumer or state.is_relative_to(consumer) or consumer.is_relative_to(state):
        raise ValueError('Producer state must be independent of the full consumer')
    if out == consumer or out.is_relative_to(consumer) or out == producer or out.is_relative_to(producer):
        raise ValueError('Runner evidence must be outside both strategy checkouts')
    if consumer.is_relative_to(out) or producer.is_relative_to(out):
        raise ValueError('Runner evidence cannot contain a strategy checkout')
    return consumer, producer, state, out


def preflight(consumer, target, consumer_sha, out, run, recovery_plan=None):
    date.fromisoformat(target)
    if len(consumer_sha) != 40 or any(c not in '0123456789abcdef' for c in consumer_sha):
        raise ValueError('Exact consumer commit required')
    temporary = out / ('preflight-' + uuid.uuid4().hex + '.json')
    argv=[sys.executable,'-B','-c',PREFLIGHT,consumer,target,consumer_sha,temporary]
    if recovery_plan is not None:
        argv.append(Path(recovery_plan).resolve())
    run('preflight', argv)
    result = json.loads(temporary.read_bytes())
    if result['target'] != target or result['consumer_sha'] != consumer_sha:
        raise ValueError('Native preflight returned another source/date')
    return result


def boundary_spec(out, day):
    directory = out / 'boundaries' / day
    fetch_snapshot(directory, day)
    files = {p.name:sha(p.read_bytes()) for p in directory.iterdir() if p.name.startswith('page-') and p.suffix=='.json'}
    spec = dict(source_root=str(out), directory=directory.relative_to(out).as_posix(), files=files)
    spec_path = out / ('boundary-' + day + '.json')
    if spec_path.exists() and spec_path.read_bytes()!=encode(spec):
        original=json.loads(spec_path.read_bytes())
        if original.get('directory')!=spec['directory'] or original.get('files')!=spec['files']:
            raise ValueError('Preserved original boundary differs from the admitted source pages')
        # Relocation creates a separately bound path instruction; it never
        # changes the original boundary object or observation receipts.
        spec_path=out/('boundary-'+day+'-admitted-'+sha(encode(spec))[:16]+'.json')
    save_once(spec_path, encode(spec))
    return spec, spec_path


def collect_boundaries(consumer, target, out, native, run):
    result = {}
    dates = sorted(set(native['rebalance_dates']) | set(native.get('recovery_rebalance_dates', [])))
    for day in dates:
        spec, spec_path = boundary_spec(out, day)
        run('boundary-' + day, [sys.executable, '-B', '-c', BOUNDARY_READBACK, consumer, day, spec_path])
        result[day] = spec
    receipt = dict(schema='microcap_scoped_early_boundaries_v1', target=target,
                   consumer_sha=native['consumer_sha'], dates=dates, boundaries=result,
                   source_only=True, formal_delivery=False, email_sent=False, orders_placed=False)
    path=Path(out)/'early-boundaries.json'
    if path.exists() and path.read_bytes()!=encode(receipt):
        previous=json.loads(path.read_bytes())
        if previous.get('target')!=target or previous.get('dates')!=dates:
            raise ValueError('Early boundary target changed inside a preserved preparation')
        path=Path(out)/('early-boundaries-admitted-'+sha(encode(receipt))[:16]+'.json')
    save_once(path, encode(receipt))
    return result


def boundary(*, consumer, target, consumer_sha, out, recovery_plan=None, resume_state=None,
             producer=None,state=None,delivery_out=None,run=None):
    consumer, out = Path(consumer).resolve(), Path(out).resolve()
    if out == consumer or out.is_relative_to(consumer) or consumer.is_relative_to(out):
        raise ValueError('Boundary evidence must be separate from its consumer')
    out.mkdir(parents=True, exist_ok=True)
    run = run or Commands(out)
    with writer_lock(out / 'runner.lock'):
        native = preflight(consumer, target, consumer_sha, out, run, recovery_plan)
        if resume_state is not None:
            if producer is None or state is None:
                raise ValueError('Boundary recovery admission needs its dedicated source runtime/state')
            admit_sources(resume_state,consumer,producer,state,out,
                          Path(delivery_out) if delivery_out else out.parent/'scoped-delivery',target,consumer_sha,run)
            native=preflight(consumer,target,consumer_sha,out,run,recovery_plan)
        collect_boundaries(consumer, target, out, native, run)
    return json.loads((out / 'early-boundaries.json').read_bytes())


def mapped_path(value, admission=None):
    text=str(value).replace('\\','/')
    for old,new in sorted((admission or {}).get('path_admission',{}).items(),key=lambda p:-len(p[0])):
        old=old.replace('\\','/').rstrip('/')
        if text==old or text.startswith(old+'/'):
            root=Path(new).resolve();path=(root/text[len(old):].lstrip('/')).resolve()
            if not path.is_relative_to(root):
                raise ValueError('Original path escapes its declared recovery admission')
            return path
    return Path(value).resolve()


def admission_record(out, target):
    path=Path(out)/'resume-admission.json'
    if not path.exists():
        return None
    value=json.loads(path.read_bytes())
    if (value.get('schema')!='microcap_scoped_resume_admission_v1' or value.get('target')!=target
            or value.get('original_json_and_clocks_rewritten') is not False):
        raise ValueError('Recovery path admission has another target or altered original clocks')
    return value


def admit_sources(manifest,consumer,producer,state,prepare_out,delivery_out,target,consumer_sha,run):
    from restore_microcap_rebalance_recovery import admit_resume
    producer_sha=json.loads((SCRIPTS.parent/'config/microcap_local_producer_runtime.json').read_bytes())['runtime_sha']
    return admit_resume(manifest,consumer=consumer,producer=producer,state=state,prepare_out=prepare_out,
                        delivery_out=delivery_out,target=target,consumer_sha=consumer_sha,producer_sha=producer_sha,run=run)


def validate_prepared(receipt, consumer, target, consumer_sha, admission=None):
    receipt = Path(receipt).resolve()
    value = json.loads(receipt.read_bytes())
    if (value.get('schema') != 'microcap_scoped_rebalance_prepared_v1'
            or value.get('status') != 'prepared_sources_only' or value.get('target') != target
            or value.get('consumer_sha') != consumer_sha or mapped_path(value['consumer_root'],admission) != consumer):
        raise ValueError('Prepared rebalance belongs to another date/runtime/consumer')
    for name, digest in value['files'].items():
        path = (receipt.parent / name).resolve()
        if not path.is_relative_to(receipt.parent) or sha(path.read_bytes()) != digest:
            raise ValueError('Prepared rebalance object changed: ' + name)
    config = (receipt.parent / value['consumer_config']).resolve()
    if not config.is_relative_to(receipt.parent) or config.relative_to(receipt.parent).as_posix() not in value['files']:
        raise ValueError('Prepared consumer config is unbound')
    if admission is not None:
        original=json.loads(config.read_bytes());current=json.loads(config.read_bytes())
        for field in ('source_root','bundle','producer_metadata_receipt'):
            current[field]=str(mapped_path(original[field],admission))
        for day,spec in current.get('boundary_snapshots',{}).items():
            spec['source_root']=str(mapped_path(original['boundary_snapshots'][day]['source_root'],admission))
        if current!=original:
            active=receipt.parent/('consumer-config-admitted-'+sha(encode(current))[:16]+'.json')
            save_once(active,encode(current))
            save_once(receipt.parent/('prepared-path-admission-'+sha(encode(current))[:16]+'.json'),encode(dict(
                schema='microcap_scoped_prepared_path_admission_v1',original_prepared_sha256=sha(receipt.read_bytes()),
                original_config_sha256=sha(config.read_bytes()),admitted_config_sha256=sha(active.read_bytes()),
                original_json_rewritten=False,source_observation_clocks_rewritten=False,
                resume_manifest_sha256=admission['manifest_sha256'],path_admission=admission['path_admission'])))
            config=active
    return value, config


def prepare(*, consumer, producer, state, out, pool, target, consumer_sha, resume_notice_generation=None, warm_source_config=None, recovery_plan=None, resume_state=None, run=None):
    consumer, producer, state, out = validate_paths(consumer, producer, state, out)
    out.mkdir(parents=True, exist_ok=True)
    run = run or Commands(out)
    with writer_lock(out/'runner.lock'):
        native = preflight(consumer,target,consumer_sha,out,run,recovery_plan)
        delivery_out=out.parent/'scoped-delivery'
        if resume_state is not None:
            admit_sources(resume_state,consumer,producer,state,out,delivery_out,target,consumer_sha,run)
            native=preflight(consumer,target,consumer_sha,out,run,recovery_plan)
        admission=admission_record(delivery_out,target)
        roster, symbols, scope_sha = load_pool(pool,target)
        validate_runtime(producer,consumer)
        saved = out/'prepared.json'
        if saved.exists():
            value, _ = validate_prepared(saved,consumer,target,consumer_sha,admission)
            if value['scope_sha256'] != scope_sha or mapped_path(value['producer_root'],admission) != producer:
                raise ValueError('Preserved preparation has another roster/producer')
            return value
        # Boundary pages are real dated historical queries. Preserve them before
        # any expensive stock request, retaining their original observation time.
        boundaries = collect_boundaries(consumer,target,out,native,run)
        common = ['--root',producer,'--protected-root',consumer,'--state-root',state,'--pool',Path(pool).resolve(),'--as-of',target]
        run('bootstrap',[sys.executable,'-B',SCRIPTS/'maintain_microcap_local.py','bootstrap',*common])
        warm=dict(status='no_local_source_bundle_configured');warm_files={}
        if warm_source_config is not None:
            from microcap_scoped_settings import download_sources
            downloaded=out/'local-source-warm.zip'
            warm=download_sources(warm_source_config,downloaded,target)
            save_once(out/'local-source-download.json',encode(warm))
            warm_files['local-source-download.json']=sha((out/'local-source-download.json').read_bytes())
            if warm['status']=='verified_local_source_bundle_downloaded':
                receipt=run('warm-local-source-inputs',[sys.executable,'-B',SCRIPTS/'import_microcap_producer_inputs.py',
                    '--root',producer,'--protected-root',consumer,'--state',state,'--pool',Path(pool).resolve(),
                    '--bundle',downloaded,'--sha256',warm['sha256'],'--not-after-date',target])
                if not isinstance(receipt,dict):raise ValueError('Producer import did not return its command evidence')
                report=json.loads(Path(receipt['stdout']).read_text(encoding='utf-8').strip().splitlines()[-1])
                if (report.get('status') not in ('producer_source_inputs_imported','skipped_same_verified_producer_inputs','skipped_newer_producer_baseline','skipped_source_covered_by_formal_baseline')
                        or report.get('source_target')!=warm['target'] or report.get('bundle_sha256')!=warm['sha256']
                        or report.get('issuer_count')!=300 or report.get('source_preparation_only') is not True
                        or report.get('production_pointers_changed') is not False or report.get('native_consumer_gate_required') is not True
                        or report.get('final_strategy_delivery') is not False or report.get('email_sent') is not False or report.get('orders_placed') is not False):
                    raise ValueError('Producer import result differs from the verified local source package')
                raw=encode(report);name='receipts/local-source-import-'+sha(raw)+'.json';save_once(out/name,raw)
                warm=dict(warm,producer_import=report,import_receipt=dict(path=name,sha256=sha(raw)))
                if sha(downloaded.read_bytes())!=warm['sha256']:raise ValueError('Local warm source package changed after verified import')
                warm_files.update({name:sha(raw),'local-source-warm.zip':warm['sha256']})
                for path in (Path(receipt['stdout']).resolve(),Path(receipt['process']).resolve()):
                    if not path.is_relative_to(out):raise ValueError('Producer import command evidence escaped preparation')
                    warm_files[path.relative_to(out).as_posix()]=sha(path.read_bytes())
        resume = ['--resume-notice-generation',Path(resume_notice_generation).resolve()] if resume_notice_generation else []
        run('maintain',[sys.executable,'-B',SCRIPTS/'maintain_microcap_local.py','run',*common,*resume])
        directory = state/'runs'/target
        if not completed(directory,producer,target):
            raise ValueError('Native source maintenance has no complete verified receipt')
        result = json.loads((directory/'result.json').read_bytes())
        if result['symbols'] != symbols or result['scope_sha256'] != scope_sha:
            raise ValueError('Source maintenance escaped the verified 300-stock roster')
        bundle = out/'sources.zip'
        if not bundle.exists():
            run('export',[sys.executable,'-B',SCRIPTS/'microcap_scoped_inputs.py','export','--root',producer,
                          '--state-root',state,'--date',target,'--bundle',bundle])
        manifest, _, bundle_sha = read_bundle(bundle,target)
        if manifest['target'] != target or manifest['symbols'] != symbols or manifest['producer_context'] != result['context']:
            raise ValueError('Preserved source ZIP belongs to another run/roster')
        if hashes(producer,manifest['files']) != manifest['files']:
            raise ValueError('Source ZIP differs from current complete native source bytes')
        run('stage',[sys.executable,'-B',SCRIPTS/'microcap_scoped_inputs.py','import','--root',consumer,
                     '--date',target,'--bundle',bundle,'--sha256',bundle_sha])
        staged=json.loads((consumer/'.microcap_index_cache/local_input_imports'/bundle_sha/'receipt.json').read_bytes())
        if (staged.get('status')!='native_sources_staged' or staged.get('source_bundle_sha256')!=bundle_sha
                or staged.get('files')!=manifest['files'] or staged.get('production_pointers_changed') is not False
                or staged.get('native_late_adoption_required') is not True):
            raise ValueError('Scoped source staging did not earn its independent native readback receipt')
        metadata_raw = (directory/'metadata.json').read_bytes()
        metadata = out/'receipts'/('metadata-'+sha(metadata_raw)+'.json')
        save_once(metadata,metadata_raw)
        config = dict(schema='microcap_native_scoped_consumer_v1',source_root=str(producer),
                      bundle=str(bundle),bundle_sha256=bundle_sha,producer_metadata_receipt=str(metadata),
                      producer_metadata_receipt_sha256=sha(metadata_raw),boundary_snapshots=boundaries)
        config_path = out/'consumer-config.json'
        save_once(config_path,encode(config))
        files = {p.relative_to(out).as_posix():sha(p.read_bytes()) for p in [bundle,metadata,config_path]}
        files.update(warm_files)
        for spec in boundaries.values():
            files.update({spec['directory']+'/'+name:digest for name,digest in spec['files'].items()})
        value = dict(schema='microcap_scoped_rebalance_prepared_v1',status='prepared_sources_only',target=target,
                     consumer_root=str(consumer),producer_root=str(producer),consumer_sha=consumer_sha,
                     scope_sha256=scope_sha,issuer_count=300,consumer_config='consumer-config.json',files=files,
                     local_source_warm=warm,
                     original_preflight=native,formal_release_verified=False,email_sent=False,orders_placed=False)
        if recovery_plan is not None:
            value['recovery_plan_sha256']=sha(Path(recovery_plan).read_bytes())
        save_once(saved,encode(value))
        return value


def deliver(*, consumer, target, consumer_sha, prepared, current_checkpoint, out, recovery_plan=None, resume_state=None, run=None):
    consumer, out = Path(consumer).resolve(), Path(out).resolve()
    if recovery_plan is None and out.exists() and any(out.iterdir()):
        raise ValueError('Delivery output must be new; prior evidence is preserved')
    if out == consumer or out.is_relative_to(consumer) or consumer.is_relative_to(out):
        raise ValueError('Delivery evidence must be separate from the consumer')
    out.mkdir(parents=True,exist_ok=True)
    run = run or Commands(out)
    with writer_lock(out/'runner.lock'):
        if resume_state is not None:
            admitted=admission_record(out,target)
            if admitted is None or admitted.get('manifest_sha256')!=sha(Path(resume_state).read_bytes()):
                raise ValueError('Delivery resume requires prior verified boundary/prepare admission of these exact originals')
        schedule=preflight(consumer,target,consumer_sha,out,run,recovery_plan)
        preparation, config = validate_prepared(prepared,consumer,target,consumer_sha,admission_record(out,target))
        native = consumer/'scripts'
        run('verify-current',[sys.executable,'-B',native/'member_period_handoff.py','verify','--checkpoint',Path(current_checkpoint).resolve()])
        if recovery_plan is not None:
            return deliver_recovery(consumer=consumer,target=target,consumer_sha=consumer_sha,
                    preparation=preparation,config=config,current_checkpoint=current_checkpoint,
                    out=out,recovery_plan=recovery_plan,run=run)
        refreshed=run('refresh',[sys.executable,'-B',native/'top100_delivery.py','refresh-all','--scoped-config',config])
        run('check',[sys.executable,'-B',native/'top100_delivery.py','check'])
        # Only both original formal gates can authorize exporting a new period.
        run('bootstrap-period',[sys.executable,'-B',native/'member_period_handoff.py','bootstrap','--root',consumer,'--out',out/'candidate'])
        logs=[refreshed[k] for k in ('stdout','stderr') if refreshed and k in refreshed]
        witness=build_precision_witness(consumer,Path(current_checkpoint).resolve(),out/'candidate/checkpoint.json',out/'precision-witness',logs)
        options=['--hedge-precision-witness',witness] if witness else []
        run('handoff',[sys.executable,'-B',native/'member_period_handoff.py','handoff','--current',Path(current_checkpoint).resolve(),
                       '--candidate',out/'candidate/checkpoint.json','--session',target,'--next-session',schedule['next_session'],*options,'--out',out/'handoff'])
        run('verify-handoff',[sys.executable,'-B',native/'member_period_handoff.py','verify','--checkpoint',out/'handoff/checkpoint.json'])
        value = dict(schema='microcap_scoped_rebalance_handoff_v1',status='native_formal_handoff_verified_not_published',
                     target=target,consumer_sha=consumer_sha,checkpoint=str(out/'handoff/checkpoint.json'),
                     checkpoint_sha256=sha((out/'handoff/checkpoint.json').read_bytes()),email_sent=False,orders_placed=False)
        save_once(out/'result.json',encode(value))
        return value


def deliver_recovery(*,consumer,target,consumer_sha,preparation,config,current_checkpoint,out,recovery_plan,run):
    from microcap_scoped_stage_resume import StageResume, formal_objects, checkpoint_objects
    current_checkpoint=Path(current_checkpoint).resolve()
    current=json.loads(current_checkpoint.read_bytes())
    plan=json.loads(Path(recovery_plan).read_bytes())
    if (current.get('period_status','current')!='current'
            or current['as_of_date']>plan['parent_not_after_date']):
        raise ValueError('Recovery needs the confirmed parent before the first missing rebalance')
    checkpoint_objects(current_checkpoint)
    context=dict(schema='microcap_scoped_recovery_transaction_v1',target=target,consumer_sha=consumer_sha,
                 source_bundle_sha256=preparation['files'][preparation.get('bundle','sources.zip')],
                 scope_sha256=preparation['scope_sha256'],parent_checkpoint_sha256=sha(current_checkpoint.read_bytes()),
                 missing_rebalances=plan['required_rebalance_dates'],
                 input_object_bindings=dict(parent_checkpoint=sha(current_checkpoint.read_bytes()),
                     consumer_config=preparation['files'][preparation['consumer_config']]),
                 controller_sources={name:sha((SCRIPTS/name).read_bytes().replace(b'\r\n',b'\n')) for name in (
                     'run_microcap_scoped_rebalance.py','microcap_scoped_stage_resume.py',
                     'microcap_recovery_handoff.py','microcap_handoff_witness.py')})
    stages=StageResume(out,consumer,context,run,input_roles=dict(parent_checkpoint=current_checkpoint,consumer_config=config))
    native=consumer/'scripts'
    refreshed=stages.call('refresh',[sys.executable,'-B',native/'top100_delivery.py','refresh-all','--scoped-config',config],
                          lambda:formal_objects(consumer,target))
    stages.call('check',[sys.executable,'-B',native/'top100_delivery.py','check'],lambda:formal_objects(consumer,target))
    # Preserve the already earned whole state before a small handoff/publication
    # can fail. The workflow publishes this exact archive; it does not repack it.
    # Native pack stamps its archive manifest anew without rewriting this
    # original refresh/check manifest. Keep both identities for exact resumption.
    original_manifest=out/'formal-manifest-original.json'
    save_once(original_manifest,(consumer/'outputs/top100_delivery_manifest.json').read_bytes())
    formal_archive=out/'formal-state.zip'
    stages.call('pack-formal',[sys.executable,'-B',native/'top100_cloud_delivery.py','pack',
                '--root',consumer,'--v23-root',consumer,'--v25-root',consumer,
                '--bundle',formal_archive,'--expected-date',target],[formal_archive,original_manifest])
    candidate=out/'candidate/checkpoint.json'
    stages.call('bootstrap-period',[sys.executable,'-B',SCRIPTS/'microcap_recovery_handoff.py','bootstrap',
                '--root',consumer,'--target',target,'--out',candidate.parent],lambda:checkpoint_objects(candidate))
    logs=[refreshed[k] for k in ('stdout','stderr')]
    witness=build_precision_witness(consumer,current_checkpoint,candidate,out/'precision-witness',logs,allow_gap=True)
    options=['--hedge-precision-witness',witness] if witness else []
    handoff=out/'handoff/checkpoint.json'
    stages.call('handoff',[sys.executable,'-B',SCRIPTS/'microcap_recovery_handoff.py','recover',
                '--root',consumer,'--current-checkpoint',current_checkpoint,'--candidate',candidate,
                '--target',target,'--out',handoff.parent,*options],lambda:checkpoint_objects(handoff))
    stages.call('verify-handoff',[sys.executable,'-B',native/'member_period_handoff.py','verify',
                '--checkpoint',handoff],lambda:checkpoint_objects(handoff))
    value=dict(schema='microcap_scoped_rebalance_handoff_v1',status='native_formal_handoff_verified_not_published',
               target=target,consumer_sha=consumer_sha,checkpoint='handoff/checkpoint.json',checkpoint_sha256=sha(handoff.read_bytes()),
               recovery_rebalances=plan['required_rebalance_dates'],formal_archive='formal-state.zip',paths_relative_to_result_directory=True,
               formal_archive_sha256=sha(formal_archive.read_bytes()),email_sent=False,orders_placed=False)
    save_once(out/'result.json',encode(value))
    return value


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('boundary','prepare','deliver'))
    for name in ('consumer','out'):
        parser.add_argument('--'+name,type=Path,required=True)
    for name in ('date','consumer-sha'):
        parser.add_argument('--'+name,required=True)
    for name in ('producer','state','pool','prepared','current-checkpoint','resume-notice-generation','warm-source-config','recovery-plan','resume-state','delivery-out'):
        parser.add_argument('--'+name,type=Path)
    args=parser.parse_args()
    common=dict(consumer=args.consumer,target=args.date,consumer_sha=args.consumer_sha,out=args.out,recovery_plan=args.recovery_plan)
    if args.action=='boundary':
        result=boundary(**common,resume_state=args.resume_state,producer=args.producer,state=args.state,delivery_out=args.delivery_out)
    elif args.action=='prepare':
        if any(getattr(args,n) is None for n in ('producer','state','pool')):parser.error('prepare requires --producer --state --pool')
        result=prepare(**common,producer=args.producer,state=args.state,pool=args.pool,resume_notice_generation=args.resume_notice_generation,
            warm_source_config=args.warm_source_config,resume_state=args.resume_state)
    else:
        if args.prepared is None or args.current_checkpoint is None:parser.error('deliver requires --prepared --current-checkpoint')
        result=deliver(**common,prepared=args.prepared,current_checkpoint=args.current_checkpoint,resume_state=args.resume_state)
    print(json.dumps(result))


if __name__=='__main__':main()
