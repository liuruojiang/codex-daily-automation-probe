"""Read the one frozen pre-seal failed source run into private partial state.

This is a receiver-issued bridge, not a seal claimed to exist in the old run.
It never adopts consumer outputs, issues a signal, dispatches, or sends mail.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import zipfile

from prepare_microcap_local import atomic, encode
from microcap_scoped_whole_recovery import api_json, stream_download
from restore_microcap_verified_state import api_request

REPOSITORY = 'liuruojiang/codex-daily-automation-probe'
WORKFLOW = '.github/workflows/microcap-member-maintenance.yml'
LEGACY_HEAD = 'eecf2c8fa7afb69426e9d8293517beb22f5a8c98'
LEGACY_RUN = 43173346897
LEGACY_ATTEMPT = 1
TARGET = '2026-11-12'
CONSUMER = 'a7c1b9ac33454be4367880a0989dfc1466640503'
PRODUCER = '3fe2e15ee9e3f2ee7e25bbc328479f1fd9910d36'
READER_CONSUMER = '95d896fe58cc7cb84c0fcb7bd76534f62c9430f4'
READER_PRODUCER = '3344e403ff01e17ef45b745a704a240e0917720b'
SCOPE = '0d1b049a83afe6807f1ad61d13c6cd578ccc141d9101e2005d5bad869082b2e1'
ORIGIN = 'legacy_source_artifact_bridge_v1'
SOURCE_ROOT = '/home/runner/work/codex-daily-automation-probe/codex-daily-automation-probe'
DIAGNOSTIC_PREFIX = 'codex-daily-automation-probe/codex-daily-automation-probe/'
OBJECTS = {
    'diagnostics': (13927122479, 'microcap-scoped-maintenance-rebalance', 64 * 1024**2),
    'producer': (13926807944, 'microcap-scoped-producer-recovery-rebalance', 2 * 1024**3),
}
CONTROLLERS = {
    'maintain_microcap_local.py': '05ed5d169c2fc030576164847afb3a38564f85cfb2245e4b6d68893f2db6e1ab',
    'microcap_local_workers.py': 'ebcbd99442e95a13e098b29ee85b28d6d2fde5ed187e1db14011ab60ff9e4675',
    'prepare_microcap_local.py': 'c0a3e9e45e5b80797a397a957a0c17f008753be796c719de84d71aa8d747f7e5',
    'microcap_local_http.py': 'ac48a9710de8f29bfb423fdd9ad2748cc349221581f6c8fc0b5b5bf2f909718f',
}


class LegacyRecoveryError(RuntimeError):
    pass


def reader_identity(runtime_sha, producer_sha):
    """Receiver role is distinct from the frozen eec/M7/P2 source identity."""
    pair=(runtime_sha,producer_sha)
    if pair not in ((CONSUMER,PRODUCER),(READER_CONSUMER,READER_PRODUCER)):
        raise LegacyRecoveryError('legacy_exact_receiver_role_differs')
    value=dict(schema='legacy_source_reader_v1',source_consumer_sha=CONSUMER,source_producer_sha=PRODUCER,
               reader_consumer_sha=runtime_sha,reader_producer_sha=producer_sha,
               source_inputs_only=True,original_source_identity_preserved=True)
    if pair==(READER_CONSUMER,READER_PRODUCER):
        from import_microcap_producer_inputs import producer_profile, exact_p2_successor
        old=producer_profile('microcap_local_producer_p2_20261112.json',PRODUCER)['calculation_sources']
        new=producer_profile('microcap_local_producer_runtime.json',READER_PRODUCER)['calculation_sources']
        value['producer_source_review']=exact_p2_successor(PRODUCER,old,new,READER_PRODUCER)
    return value


def sha_file(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _pages(url, token, key):
    values = []
    for page in range(1, 101):
        payload = api_json(url + f'?per_page=100&page={page}', token)
        part = payload.get(key) if isinstance(payload, dict) else None
        if not isinstance(part, list):
            raise LegacyRecoveryError('legacy_api_page_invalid')
        values.extend(part)
        if len(part) < 100:
            return values
    raise LegacyRecoveryError('legacy_api_pagination_incomplete')


def discover(*, token, runtime_sha, producer_sha, not_after):
    """GET only; no general legacy name/branch/runtime fallback is allowed."""
    if ((runtime_sha,producer_sha) not in ((CONSUMER,PRODUCER),(READER_CONSUMER,READER_PRODUCER))
            or date.fromisoformat(not_after).isoformat() < TARGET):
        return None
    reader_identity(runtime_sha,producer_sha)
    if not token:
        raise LegacyRecoveryError('legacy_repository_authentication_required')
    base = f'https://api.github.com/repos/{REPOSITORY}/actions'
    run = api_json(f'{base}/runs/{LEGACY_RUN}', token)
    if (run.get('id') != LEGACY_RUN or run.get('run_attempt') != LEGACY_ATTEMPT
            or run.get('head_sha') != LEGACY_HEAD or run.get('head_branch') != 'main'
            or run.get('event') != 'workflow_dispatch' or str(run.get('path', '')).split('@')[0] != WORKFLOW
            or run.get('status') != 'completed' or run.get('conclusion') != 'failure'
            or any((run.get(k) or {}).get('full_name') != REPOSITORY for k in ('repository', 'head_repository'))):
        raise LegacyRecoveryError('legacy_frozen_source_run_differs')
    jobs = _pages(f'{base}/runs/{LEGACY_RUN}/attempts/{LEGACY_ATTEMPT}/jobs', token, 'jobs')
    scoped = [j for j in jobs if j.get('name') == 'scoped']
    if len(scoped) != 1:
        raise LegacyRecoveryError('legacy_scoped_attempt_missing_or_ambiguous')
    job = scoped[0]
    if (job.get('run_id') != LEGACY_RUN or job.get('run_attempt') != LEGACY_ATTEMPT
            or job.get('head_sha') != LEGACY_HEAD or job.get('status') != 'completed'
            or job.get('conclusion') != 'failure'):
        raise LegacyRecoveryError('legacy_scoped_attempt_differs')
    for name, conclusion in (
            ('Prepare current close sources for exactly 300 candidates', 'failure'),
            ('Preserve scoped maintenance evidence', 'success'),
            ('Preserve failed scoped producer sources', 'success')):
        steps = [s for s in job.get('steps', []) if s.get('name') == name]
        if len(steps) != 1 or steps[0].get('status') != 'completed' or steps[0].get('conclusion') != conclusion:
            raise LegacyRecoveryError('legacy_source_step_evidence_differs')
    for name in ('Earn original formal rebalance and next-session handoff',
                 'Pack verified scoped whole state', 'Publish verified member checkpoint',
                 'Publish verified scoped whole state'):
        steps = [s for s in job.get('steps', []) if s.get('name') == name]
        if len(steps) != 1 or steps[0].get('status') != 'completed' or steps[0].get('conclusion') != 'skipped':
            raise LegacyRecoveryError('legacy_source_only_run_has_formal_execution')
    artifacts = _pages(f'{base}/runs/{LEGACY_RUN}/artifacts', token, 'artifacts')
    pair = {}
    for role, (identity, name, maximum) in OBJECTS.items():
        found = [a for a in artifacts if a.get('name') == name and a.get('expired') is False]
        if not found:
            if not any(a.get('name') == name for a in artifacts):
                raise LegacyRecoveryError('legacy_paired_artifact_missing')
            return None
        if len(found) != 1:
            raise LegacyRecoveryError('legacy_paired_artifact_ambiguous')
        item = found[0]; owner = item.get('workflow_run') or {}
        if (item.get('id') != identity or item.get('archive_download_url') != f'{base}/artifacts/{identity}/zip'
                or owner.get('id') != LEGACY_RUN or owner.get('head_sha') != LEGACY_HEAD
                or owner.get('head_branch') != 'main'
                or re.fullmatch('sha256:[0-9a-f]{64}', str(item.get('digest', ''))) is None
                or type(item.get('size_in_bytes')) is not int or not 0 < item['size_in_bytes'] <= maximum):
            raise LegacyRecoveryError('legacy_paired_artifact_identity_differs')
        pair[role] = item
    return dict(run_id=LEGACY_RUN, run_attempt=LEGACY_ATTEMPT, head_sha=LEGACY_HEAD,
                job_id=job.get('id'), workflow_path=WORKFLOW, head_branch='main', event='workflow_dispatch',
                original_conclusion='failure', original_step_evidence=job['steps'], artifacts=pair)


def source_role(name):
    from restore_microcap_rebalance_recovery import relative
    relative(name)
    return (name.startswith(('scoped-source-state/runtime/.microcap_index_cache/',
                             f'scoped-source-state/runs/{TARGET}/',
                             'scoped-prepare/commands/', 'scoped-prepare/receipts/'))
            or name in ('scoped-source-state/bootstrap.json', 'scoped-source-state/reference-proxy.csv',
                        'scoped-schedule.json', 'scoped-restore.json', 'scoped-prepare/local-source-download.json'))


def _members(archive, role):
    from restore_microcap_rebalance_recovery import relative
    names = {}; seen = set()
    if len(archive.infolist()) > 250000:
        raise LegacyRecoveryError('legacy_archive_object_budget_exceeded')
    expanded = 0
    for item in archive.infolist():
        name = item.filename.rstrip('/') if item.is_dir() else item.filename
        relative(name)
        if name.casefold() in seen or stat.S_IFMT(item.external_attr >> 16) == stat.S_IFLNK:
            raise LegacyRecoveryError('legacy_archive_duplicate_or_link')
        seen.add(name.casefold())
        if item.is_dir():
            continue
        expanded += item.file_size
        if expanded > (256 * 1024**2 if role == 'diagnostics' else 8 * 1024**3):
            raise LegacyRecoveryError('legacy_archive_expanded_budget_exceeded')
        if role == 'diagnostics':
            if name.startswith(DIAGNOSTIC_PREFIX):
                logical = name[len(DIAGNOSTIC_PREFIX):]
                if not source_role(logical):
                    raise LegacyRecoveryError('legacy_diagnostic_role_not_allowed')
            elif re.fullmatch(r'_temp/scoped-whole-recovery-[^/]+/[^/]+\.(json|log)', name):
                continue  # Preserve the original outer ZIP; this is not source adoption.
            else:
                raise LegacyRecoveryError('legacy_diagnostic_workspace_prefix_differs')
        else:
            # Old upload-artifact LCA is scoped-source-state.  Accept its exact
            # logical spelling too, never an arbitrary suffix/root search.
            logical = name if name.startswith('scoped-source-state/') else 'scoped-source-state/' + name
            if not (logical.startswith('scoped-source-state/runtime/.microcap_index_cache/')
                    or logical in ('scoped-source-state/bootstrap.json', 'scoped-source-state/reference-proxy.csv')):
                raise LegacyRecoveryError('legacy_producer_role_not_allowed')
        if logical in names:
            raise LegacyRecoveryError('legacy_logical_alias_collision')
        names[logical] = item
    return names


def validate_bridge_manifest(value):
    """Narrow the normal admission ABI to the bridge's actual source roles."""
    origin = value.get('manifest_origin')
    if origin != ORIGIN:
        raise LegacyRecoveryError('legacy_manifest_origin_differs')
    if (value.get('source_run_id') != LEGACY_RUN or value.get('source_run_attempt') != LEGACY_ATTEMPT
            or value.get('producer_commit') != LEGACY_HEAD or value.get('runtime_sha') != CONSUMER
            or value.get('producer_sha') != PRODUCER or value.get('target') != TARGET
            or value.get('source_root') != SOURCE_ROOT or value.get('phase') != 'state'
            or value.get('source_inputs_only') is not True or value.get('original_source_was_sealed') is not False
            or value.get('formal_state_archive') is not None or value.get('required_rebalance_dates') != [TARGET]
            or 'sealed_at' in value):
        raise LegacyRecoveryError('legacy_manifest_source_only_context_differs')
    issuer = value.get('manifest_issuer') or {}
    if (issuer.get('role') != 'current_receiver_bridge'
            or re.fullmatch('[0-9a-f]{40}', str(issuer.get('head_sha', ''))) is None
            or issuer.get('head_sha') == LEGACY_HEAD):
        raise LegacyRecoveryError('legacy_manifest_receiver_identity_missing')
    evidence = value.get('legacy_source_evidence') or {}
    if (evidence.get('run_id') != LEGACY_RUN or evidence.get('run_attempt') != LEGACY_ATTEMPT
            or evidence.get('head_sha') != LEGACY_HEAD or evidence.get('original_conclusion') != 'failure'):
        raise LegacyRecoveryError('legacy_manifest_original_source_evidence_differs')
    for role, (identity, name, _) in OBJECTS.items():
        item = (evidence.get('artifacts') or {}).get(role) or {}
        if (item.get('id') != identity or item.get('name') != name
                or re.fullmatch('sha256:[0-9a-f]{64}', str(item.get('digest', ''))) is None):
            raise LegacyRecoveryError('legacy_manifest_original_artifact_evidence_differs')
    if any(name != 'scoped-recovery-plan.json' and not source_role(name) for name in value.get('files', {})):
        raise LegacyRecoveryError('legacy_manifest_contains_non_source_role')


def _validate_sources(out):
    def read(name):
        return json.loads((out / name).read_bytes())
    run_prefix = f'scoped-source-state/runs/{TARGET}/'
    result = read(run_prefix + 'result.json'); assignment = read(run_prefix + 'worker-context.json')
    context = result.get('context') or {}; symbols = result.get('symbols')
    from import_microcap_producer_inputs import producer_profile
    config = producer_profile('microcap_local_producer_p2_20261112.json',PRODUCER)
    if (config.get('runtime_sha') != PRODUCER or config.get('role') != 'source_preparation_only'
            or result.get('status') != 'incomplete' or result.get('target') != TARGET
            or result.get('processed') != 300 or result.get('total') != 300
            or not isinstance(symbols, list) or len(symbols) != 300 or len(set(symbols)) != 300
            or any(re.fullmatch('[0-9]{6}', str(s)) is None for s in symbols)
            or assignment.get('symbols') != symbols or assignment.get('context') != context
            or context.get('target') != TARGET or context.get('runtime_sha') != PRODUCER
            or context.get('scope_sha256') != SCOPE or result.get('scope_sha256') != SCOPE
            or context.get('observation_owner_date') != TARGET
            or context.get('observation_floor') != TARGET + 'T09:00:00+08:00'
            or context.get('native_sources') != config['calculation_sources']
            or context.get('orchestrator_sources') != CONTROLLERS
            or context.get('orchestrator_sha256') != CONTROLLERS['maintain_microcap_local.py']):
        raise LegacyRecoveryError('legacy_source_context_or_roster_differs')
    bootstrap = read('scoped-source-state/bootstrap.json')
    if (bootstrap.get('status') != 'complete' or bootstrap.get('runtime_sha') != PRODUCER
            or bootstrap.get('source_root') != SOURCE_ROOT + '/scoped-consumer'
            or bootstrap.get('symbols') != symbols
            or bootstrap.get('reference_proxy_sha256') != sha_file(out/'scoped-source-state/reference-proxy.csv')
            or context.get('master_sha256') != sha_file(out/'scoped-source-state/runtime/.microcap_index_cache/security_master.csv')):
        raise LegacyRecoveryError('legacy_bootstrap_or_original_master_differs')
    schedule = read('scoped-schedule.json')
    if schedule.get('date') != TARGET or schedule.get('is_rebalance') is not True or schedule.get('is_trading_day') is not True:
        raise LegacyRecoveryError('legacy_original_target_schedule_differs')
    commands = list((out/'scoped-prepare/commands').glob('maintain-*/process.json'))
    if len(commands) != 1:
        raise LegacyRecoveryError('legacy_original_maintain_process_missing_or_ambiguous')
    process = json.loads(commands[0].read_bytes()); command = process.get('command') or []
    if (process.get('exit_code') != 1 or process.get('completion_unconfirmed')
            or not isinstance(command,list) or command.count('--as-of') != 1
            or command.index('--as-of')+1 >= len(command) or command[command.index('--as-of')+1] != TARGET
            or SOURCE_ROOT+'/automation/scripts/maintain_microcap_local.py' not in command):
        raise LegacyRecoveryError('legacy_original_maintain_failure_differs')
    counts = Counter()
    for key in [s+'-'+stage for s in symbols for stage in ('price', 'share', 'ratio')] + ['notice', 'current-names']:
        record = read(run_prefix + key + '.json')
        if record.get('key') != key or record.get('context') != context:
            raise LegacyRecoveryError('legacy_source_stage_context_differs')
        status = record.get('status'); counts[(key.rsplit('-', 1)[-1] if key not in ('notice','current-names') else key, status)] += 1
        if status == 'complete':
            files = record.get('files')
            if not isinstance(files, dict) or not files:
                raise LegacyRecoveryError('legacy_complete_stage_has_no_object_closure')
            for name, digest in files.items():
                from restore_microcap_rebalance_recovery import relative
                relative(name)
                if (not name.startswith('.microcap_index_cache/') or re.fullmatch('[0-9a-f]{64}', str(digest)) is None
                        or not (out/'scoped-source-state/runtime'/name).is_file()
                        or sha_file(out/'scoped-source-state/runtime'/name) != digest):
                    raise LegacyRecoveryError('legacy_successful_source_object_missing_or_changed')
        elif status != 'failed':
            raise LegacyRecoveryError('legacy_stage_terminal_state_unknown')
    expected = {('price','complete'):300,('ratio','complete'):300,('share','complete'):44,
                ('share','failed'):256,('notice','failed'):1,('current-names','complete'):1}
    if dict(counts) != expected or result.get('complete') != 44 or result.get('metadata_status') != 'waiting_for_notice_or_current_names':
        raise LegacyRecoveryError('legacy_actual_partial_stage_summary_differs')
    return dict(successful_stock_stages=644, failed_share_stages=256, notice_status='failed',
                metadata_status='waiting_for_notice_or_current_names', current_names_status='complete',
                original_context_sha256=hashlib.sha256(encode(context)).hexdigest(), scope_sha256=SCOPE)


def recover(*, token, runtime_sha, producer_sha, not_after, out, issuer_head=None):
    from restore_microcap_rebalance_recovery import MANIFESTS, SCHEMA, verify_manifest, verify_plan_binding
    out = Path(out).resolve()
    if out.exists():
        raise LegacyRecoveryError('preserve_existing_legacy_recovery_destination')
    source = discover(token=token,runtime_sha=runtime_sha,producer_sha=producer_sha,not_after=not_after)
    if source is None:
        return dict(restored=False,status='no_frozen_legacy_source_pair')
    reader=reader_identity(runtime_sha,producer_sha)
    archives = {}
    for role, item in source['artifacts'].items():
        archive = out.with_name(out.name + '-legacy-' + role + '-' + str(item['id']) + '.zip')
        stream_download(api_request(item['archive_download_url'],token),archive,
                        expected_sha=item['digest'].split(':')[1],expected_bytes=item['size_in_bytes'])
        if archive.stat().st_size != item['size_in_bytes'] or sha_file(archive) != item['digest'].split(':')[1]:
            raise LegacyRecoveryError('legacy_downloaded_original_outer_identity_differs')
        archives[role] = archive
    with zipfile.ZipFile(archives['diagnostics']) as diagnostic, zipfile.ZipFile(archives['producer']) as producer:
        mappings = {'diagnostics':_members(diagnostic,'diagnostics'), 'producer':_members(producer,'producer')}
        expanded = sum(info.file_size for mapping in mappings.values() for info in mapping.values())
        if shutil.disk_usage(out.parent).free < expanded + 4 * 1024**3:
            raise LegacyRecoveryError('legacy_recovery_disk_reserve_insufficient')
        out.mkdir(parents=True,exist_ok=False)
        files = {}; sizes = {}
        for role, archive in (('diagnostics',diagnostic),('producer',producer)):
            for logical, item in mappings[role].items():
                path=out/logical; path.parent.mkdir(parents=True,exist_ok=True)
                if path.exists():
                    with archive.open(item) as raw:
                        if path.read_bytes() != raw.read():
                            raise LegacyRecoveryError('legacy_paired_original_object_conflict')
                    continue
                with archive.open(item) as raw, path.open('xb') as destination:
                    shutil.copyfileobj(raw,destination,1024**2)
                if path.stat().st_size != item.file_size:
                    raise LegacyRecoveryError('legacy_original_object_incomplete')
                files[logical]=sha_file(path); sizes[logical]=path.stat().st_size
    summary = _validate_sources(out)
    issuer_head = issuer_head or os.environ.get('GITHUB_SHA') or subprocess.check_output(
        ['git','-C',str(Path(__file__).resolve().parents[1]),'rev-parse','HEAD'],encoding='utf-8').strip()
    if re.fullmatch('[0-9a-f]{40}',issuer_head) is None or issuer_head == LEGACY_HEAD:
        raise LegacyRecoveryError('legacy_receiver_commit_required')
    generated = dict(manifest_origin=ORIGIN, original_source_was_sealed=False,
                     manifest_issuer=dict(role='current_receiver_bridge',head_sha=issuer_head),
                     bridged_at=datetime.now(timezone.utc).isoformat(),legacy_source_evidence=source)
    provenance='scoped-prepare/receipts/legacy-source-bridge.json'
    if (out/provenance).exists():
        raise LegacyRecoveryError('preserve_existing_legacy_receiver_provenance')
    atomic(out/provenance,encode(dict(generated,summary=summary,source_inputs_only=True,
                                     status='resumable_partial',final_strategy_delivery=False)))
    plan = dict(schema='microcap_scoped_recovery_plan_v1',consumer_sha=CONSUMER,delivery_date=TARGET,
                required_rebalance_dates=[TARGET],manifest_origin=ORIGIN,
                purpose='receiver_source_binding_only_not_an_original_execution_plan')
    atomic(out/'scoped-recovery-plan.json',encode(plan))
    for name in (provenance,'scoped-recovery-plan.json'):
        files[name]=sha_file(out/name);sizes[name]=(out/name).stat().st_size
    manifest=dict(generated,schema=SCHEMA,status='resumable_partial',phase='state',
        runtime_sha=CONSUMER,producer_sha=PRODUCER,producer_commit=LEGACY_HEAD,source_run_id=LEGACY_RUN,
        source_run_attempt=LEGACY_ATTEMPT,source_root=SOURCE_ROOT,target=TARGET,required_rebalance_dates=[TARGET],
        source_inputs_only=True,files=files,sizes=sizes,plan_sha256=files['scoped-recovery-plan.json'],
        formal_state_archive=None,native_adoption_required=True,final_strategy_delivery=False,email_sent=False,orders_placed=False)
    validate_bridge_manifest(manifest);verify_manifest(manifest,CONSUMER,PRODUCER,not_after)
    verify_plan_binding(manifest,(out/'scoped-recovery-plan.json').read_bytes())
    manifest_path=out/MANIFESTS['state'];atomic(manifest_path,encode(manifest))
    receipt=dict(status='legacy_source_partial_recovery_downloaded_verified_not_adopted',restored=True,
        manifest=str(manifest_path),manifest_sha256=sha_file(manifest_path),manifest_origin=ORIGIN,
        source_run_id=LEGACY_RUN,source_run_attempt=LEGACY_ATTEMPT,producer_commit=LEGACY_HEAD,
        original_target=TARGET,requested_not_after=not_after,formal_archive='',summary=summary,
        legacy_source_evidence=source,validation_reader=reader,native_adoption_required=True,
        final_strategy_delivery=False,email_sent=False,orders_placed=False)
    atomic(out/'recovery-receipt.json',encode(receipt))
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('discover','recover'))
    parser.add_argument('--runtime-sha',default=CONSUMER);parser.add_argument('--producer-sha',default=PRODUCER)
    parser.add_argument('--not-after-date',required=True);parser.add_argument('--out',type=Path)
    args=parser.parse_args()
    common=dict(token=os.environ.get('GITHUB_TOKEN'),runtime_sha=args.runtime_sha,
                producer_sha=args.producer_sha,not_after=args.not_after_date)
    if args.action=='recover':
        if args.out is None:parser.error('recover requires --out')
        value=recover(**common,out=args.out)
    else:value=discover(**common)
    print(json.dumps(value,ensure_ascii=False))


if __name__=='__main__':
    main()
