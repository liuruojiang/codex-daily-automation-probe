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

SCRIPTS = Path(__file__).resolve().parent

# Each native call runs in its own interpreter: producer/native script modules
# from different exact Git checkouts must never share Python's import cache.
PREFLIGHT = r'''
import json,sys,subprocess
from pathlib import Path
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
if not s['is_trading_day'] or not s['is_rebalance']:raise ValueError('Not an original native rebalance session')
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
        return dict(stdout=attempt/'stdout.log',process=attempt/'process.json')


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


def preflight(consumer, target, consumer_sha, out, run):
    date.fromisoformat(target)
    if len(consumer_sha) != 40 or any(c not in '0123456789abcdef' for c in consumer_sha):
        raise ValueError('Exact consumer commit required')
    temporary = out / ('preflight-' + uuid.uuid4().hex + '.json')
    run('preflight', [sys.executable,'-B','-c',PREFLIGHT,consumer,target,consumer_sha,temporary])
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
    save_once(spec_path, encode(spec))
    return spec, spec_path


def validate_prepared(receipt, consumer, target, consumer_sha):
    receipt = Path(receipt).resolve()
    value = json.loads(receipt.read_bytes())
    if (value.get('schema') != 'microcap_scoped_rebalance_prepared_v1'
            or value.get('status') != 'prepared_sources_only' or value.get('target') != target
            or value.get('consumer_sha') != consumer_sha or Path(value['consumer_root']).resolve() != consumer):
        raise ValueError('Prepared rebalance belongs to another date/runtime/consumer')
    for name, digest in value['files'].items():
        path = (receipt.parent / name).resolve()
        if not path.is_relative_to(receipt.parent) or sha(path.read_bytes()) != digest:
            raise ValueError('Prepared rebalance object changed: ' + name)
    config = (receipt.parent / value['consumer_config']).resolve()
    if not config.is_relative_to(receipt.parent) or config.relative_to(receipt.parent).as_posix() not in value['files']:
        raise ValueError('Prepared consumer config is unbound')
    return value, config


def prepare(*, consumer, producer, state, out, pool, target, consumer_sha, resume_notice_generation=None, warm_source_config=None, run=None):
    consumer, producer, state, out = validate_paths(consumer, producer, state, out)
    out.mkdir(parents=True, exist_ok=True)
    run = run or Commands(out)
    with writer_lock(out/'runner.lock'):
        native = preflight(consumer,target,consumer_sha,out,run)
        roster, symbols, scope_sha = load_pool(pool,target)
        validate_runtime(producer,consumer)
        saved = out/'prepared.json'
        if saved.exists():
            value, _ = validate_prepared(saved,consumer,target,consumer_sha)
            if value['scope_sha256'] != scope_sha or value['producer_root'] != str(producer):
                raise ValueError('Preserved preparation has another roster/producer')
            return value
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
                if (report.get('status') not in ('producer_source_inputs_imported','skipped_same_verified_producer_inputs','skipped_newer_producer_baseline')
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
        boundaries = {}
        for day in native['rebalance_dates']:
            spec, spec_path = boundary_spec(out,day)
            run('boundary-'+day,[sys.executable,'-B','-c',BOUNDARY_READBACK,consumer,day,spec_path])
            boundaries[day] = spec
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
        save_once(saved,encode(value))
        return value


def deliver(*, consumer, target, consumer_sha, prepared, current_checkpoint, out, run=None):
    consumer, out = Path(consumer).resolve(), Path(out).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError('Delivery output must be new; prior evidence is preserved')
    if out == consumer or out.is_relative_to(consumer) or consumer.is_relative_to(out):
        raise ValueError('Delivery evidence must be separate from the consumer')
    out.mkdir(parents=True,exist_ok=True)
    run = run or Commands(out)
    with writer_lock(out/'runner.lock'):
        schedule=preflight(consumer,target,consumer_sha,out,run)
        _, config = validate_prepared(prepared,consumer,target,consumer_sha)
        native = consumer/'scripts'
        run('verify-current',[sys.executable,'-B',native/'member_period_handoff.py','verify','--checkpoint',Path(current_checkpoint).resolve()])
        run('refresh',[sys.executable,'-B',native/'top100_delivery.py','refresh-all','--scoped-config',config])
        run('check',[sys.executable,'-B',native/'top100_delivery.py','check'])
        # Only both original formal gates can authorize exporting a new period.
        run('bootstrap-period',[sys.executable,'-B',native/'member_period_handoff.py','bootstrap','--root',consumer,'--out',out/'candidate'])
        run('handoff',[sys.executable,'-B',native/'member_period_handoff.py','handoff','--current',Path(current_checkpoint).resolve(),
                       '--candidate',out/'candidate/checkpoint.json','--session',target,'--next-session',schedule['next_session'],'--out',out/'handoff'])
        run('verify-handoff',[sys.executable,'-B',native/'member_period_handoff.py','verify','--checkpoint',out/'handoff/checkpoint.json'])
        value = dict(schema='microcap_scoped_rebalance_handoff_v1',status='native_formal_handoff_verified_not_published',
                     target=target,consumer_sha=consumer_sha,checkpoint=str(out/'handoff/checkpoint.json'),
                     checkpoint_sha256=sha((out/'handoff/checkpoint.json').read_bytes()),email_sent=False,orders_placed=False)
        save_once(out/'result.json',encode(value))
        return value


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('prepare','deliver'))
    for name in ('consumer','out'):
        parser.add_argument('--'+name,type=Path,required=True)
    for name in ('date','consumer-sha'):
        parser.add_argument('--'+name,required=True)
    for name in ('producer','state','pool','prepared','current-checkpoint','resume-notice-generation','warm-source-config'):
        parser.add_argument('--'+name,type=Path)
    args=parser.parse_args()
    common=dict(consumer=args.consumer,target=args.date,consumer_sha=args.consumer_sha,out=args.out)
    if args.action=='prepare':
        if any(getattr(args,n) is None for n in ('producer','state','pool')):parser.error('prepare requires --producer --state --pool')
        result=prepare(**common,producer=args.producer,state=args.state,pool=args.pool,resume_notice_generation=args.resume_notice_generation,
            warm_source_config=args.warm_source_config)
    else:
        if args.prepared is None or args.current_checkpoint is None:parser.error('deliver requires --prepared --current-checkpoint')
        result=deliver(**common,prepared=args.prepared,current_checkpoint=args.current_checkpoint)
    print(json.dumps(result))


if __name__=='__main__':main()
