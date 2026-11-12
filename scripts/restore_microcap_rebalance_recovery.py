"""Seal and recover private partial scoped state; never declare it complete.

Only exact main-workflow artifacts are discoverable.  A failed producer is a
permitted source of *partial* evidence, not proof of formal delivery.  Native
readback and adoption remain the caller's responsibility after extraction.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import uuid
import zipfile

from prepare_microcap_local import atomic, encode, writer_lock
from microcap_scoped_whole_recovery import api_json, stream_download
from restore_microcap_verified_state import api_request

REPOSITORY = 'liuruojiang/codex-daily-automation-probe'
WORKFLOW = '.github/workflows/microcap-member-maintenance.yml'
SCHEMA = 'microcap_scoped_resume_v1'
MANIFESTS = {'boundaries': 'scoped-boundary-manifest.json', 'state': 'scoped-recovery-manifest.json'}
PREFIXES = {'boundaries': 'microcap-scoped-boundaries-v1-', 'state': 'microcap-scoped-resume-v1-'}
UPLOAD_STEPS = {'boundaries': 'Preserve original R boundary recovery evidence',
                'state': 'Preserve resumable scoped recovery state'}
MAX_BYTES = 2 * 1024**3
MAX_EXPANDED = 16 * 1024**3
MAX_FILES = 250000
ROOT_FILES = {'scoped-recovery-plan.json', 'scoped-schedule.json', 'scoped-restore.json',
              'scoped-latest-restore.json', 'scoped-parent-restore.json'}
DIRECTORIES = ('scoped-prepare', 'scoped-delivery', 'scoped-latest', 'scoped-previous',
               'scoped-source-state/runs', 'scoped-source-state/runtime/.microcap_index_cache',
               'scoped-consumer/.microcap_index_cache', 'scoped-consumer/outputs')
STATE_FILES = {'scoped-source-state/bootstrap.json', 'scoped-source-state/reference-proxy.csv'}


class RecoveryError(RuntimeError):
    """A bounded non-secret diagnostic suitable for a preserved receipt."""


def sha_file(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def commit(value):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{40}', value) is None:
        raise RecoveryError('recovery_exact_commit_required')
    return value


def relative(value):
    if not isinstance(value, str) or not value:
        raise RecoveryError('recovery_unsafe_object_path')
    path = PurePosixPath(value)
    if (path.is_absolute() or '\\' in value
            or ':' in value or '..' in path.parts or path.as_posix() != value
            or any(p.endswith(('.', ' ')) for p in path.parts)
            or any(p.lower() in {'.git', '.ssh', '.aws', '.env'} for p in path.parts)
            or value.endswith('.lock')):
        raise RecoveryError('recovery_unsafe_object_path')
    return path


def allowed(value, phase):
    relative(value)
    if value.startswith('scoped-delivery/publication-readback/'):
        return False
    if phase == 'boundaries':
        name = value[len('scoped-prepare/'):] if value.startswith('scoped-prepare/') else ''
        return value in ROOT_FILES or value.startswith('scoped-prepare/boundaries/') or (
            '/' not in name and name.endswith('.json')
            and (name.startswith(('boundary-', 'preflight-')) or name in ('recovery-intent.json', 'early-boundaries.json')))
    return value in ROOT_FILES or value in STATE_FILES or any(value.startswith(p + '/') for p in DIRECTORIES)


def verify_manifest(value, runtime_sha, producer_sha, not_after):
    if (not isinstance(value, dict) or value.get('schema') != SCHEMA
            or value.get('status') != 'resumable_partial' or value.get('phase') not in MANIFESTS
            or value.get('runtime_sha') != commit(runtime_sha)
            or value.get('producer_sha') != commit(producer_sha)
            or type(value.get('source_run_id')) is not int or value['source_run_id'] <= 0
            or type(value.get('source_run_attempt')) is not int or value['source_run_attempt'] <= 0
            or not isinstance(value.get('source_root'), str) or not value['source_root']
            or value.get('native_adoption_required') is not True
            or value.get('final_strategy_delivery') is not False
            or value.get('email_sent') is not False or value.get('orders_placed') is not False):
        raise RecoveryError('recovery_manifest_context_differs')
    commit(value.get('producer_commit'))
    target = date.fromisoformat(value['target']).isoformat()
    if target != value['target'] or target > date.fromisoformat(not_after).isoformat():
        raise RecoveryError('recovery_manifest_target_is_future')
    files, sizes = value.get('files'), value.get('sizes')
    if (not isinstance(files, dict) or not files or len(files) > MAX_FILES
            or not isinstance(sizes, dict) or set(files) != set(sizes)
            or re.fullmatch('[0-9a-f]{64}', value.get('plan_sha256', '')) is None):
        raise RecoveryError('recovery_manifest_not_closed')
    casefold = set()
    for name, digest in files.items():
        if (not allowed(name, value['phase']) or name.casefold() in casefold
                or re.fullmatch('[0-9a-f]{64}', digest or '') is None
                or type(sizes[name]) is not int or sizes[name] < 0):
            raise RecoveryError('recovery_manifest_object_invalid')
        casefold.add(name.casefold())
    if sum(sizes.values()) > MAX_EXPANDED:
        raise RecoveryError('recovery_expanded_budget_exceeded')
    if files.get('scoped-recovery-plan.json') != value['plan_sha256']:
        raise RecoveryError('recovery_plan_is_not_bound')
    formal = value.get('formal_state_archive')
    if formal is not None and (
            not isinstance(formal, dict) or formal.get('path') != 'scoped-delivery/formal-state.zip'
            or formal.get('sha256') != files.get(formal['path'])
            or formal.get('bytes') != sizes.get(formal['path'])):
        raise RecoveryError('recovery_formal_archive_not_bound')
    return value


def verify_plan_binding(manifest, raw):
    plan = json.loads(raw)
    if (hashlib.sha256(raw).hexdigest() != manifest['plan_sha256']
            or plan.get('schema') != 'microcap_scoped_recovery_plan_v1'
            or plan.get('consumer_sha') != manifest['runtime_sha']
            or plan.get('delivery_date') != manifest['target']
            or plan.get('required_rebalance_dates') != manifest['required_rebalance_dates']):
        raise RecoveryError('recovery_original_plan_context_differs')


def seal(*, root, runtime_sha, producer_sha, plan, out, phase, run_id, run_attempt, producer_commit):
    root, plan, out = Path(root).resolve(), Path(plan).resolve(), Path(out).resolve()
    if phase not in MANIFESTS or out.name != MANIFESTS[phase] or not out.is_relative_to(root):
        raise RecoveryError('recovery_manifest_destination_differs')
    if plan != root / 'scoped-recovery-plan.json':
        raise RecoveryError('recovery_plan_requires_its_fixed_logical_role')
    original_plan = json.loads(plan.read_bytes())
    if original_plan.get('consumer_sha') != runtime_sha or original_plan.get('schema') != 'microcap_scoped_recovery_plan_v1':
        raise RecoveryError('recovery_plan_runtime_differs')
    if out.exists():
        old = verify_manifest(json.loads(out.read_bytes()), runtime_sha, producer_sha, original_plan['delivery_date'])
        if (old['source_run_id'], old['source_run_attempt'], old['producer_commit'], old['plan_sha256']) != (
                run_id, run_attempt, producer_commit, sha_file(plan)):
            raise RecoveryError('preserve_existing_recovery_manifest')
        if any(sha_file(root / name) != digest for name, digest in old['files'].items()):
            raise RecoveryError('sealed_recovery_object_changed')
        return old
    formal = root / 'scoped-delivery/formal-state.zip'
    directories = DIRECTORIES if phase == 'state' else ('scoped-prepare',)
    selected = {root / path for path in ROOT_FILES | STATE_FILES if (root / path).is_file()}
    for directory in directories:
        folder = root / directory
        if formal.is_file() and directory.startswith('scoped-consumer/'):
            continue
        if folder.exists():
            for path in folder.rglob('*'):
                if path.is_symlink():
                    raise RecoveryError('recovery_source_contains_link')
                if path.is_file() and not path.name.endswith('.lock'):
                    selected.add(path)
    files, sizes = {}, {}
    for path in sorted(selected):
        name = path.relative_to(root).as_posix()
        if name.startswith('scoped-delivery/publication-readback/'):
            continue
        if phase == 'boundaries' and not allowed(name, phase):
            continue
        if not allowed(name, phase) or path.is_symlink():
            raise RecoveryError('recovery_source_object_not_allowed')
        files[name] = sha_file(path); sizes[name] = path.stat().st_size
    value = dict(schema=SCHEMA, status='resumable_partial', phase=phase,
                 runtime_sha=runtime_sha, producer_sha=producer_sha,
                 producer_commit=producer_commit, source_run_id=run_id, source_run_attempt=run_attempt,
                 source_root=str(root), target=original_plan['delivery_date'],
                 required_rebalance_dates=original_plan['required_rebalance_dates'], plan_sha256=sha_file(plan),
                 files=files, sizes=sizes, sealed_at=datetime.now(timezone.utc).isoformat(),
                 formal_state_archive=(dict(path='scoped-delivery/formal-state.zip',
                     sha256=files['scoped-delivery/formal-state.zip'], bytes=sizes['scoped-delivery/formal-state.zip'])
                     if phase == 'state' and formal.is_file() else None),
                 native_adoption_required=True, final_strategy_delivery=False, email_sent=False, orders_placed=False)
    verify_manifest(value, runtime_sha, producer_sha, original_plan['delivery_date'])
    atomic(out, encode(value))
    return value


def uploaded(base, token, run_id, phase):
    for page in range(1, 101):
        payload = api_json(f'{base}/runs/{run_id}/jobs?per_page=100&page={page}', token)
        jobs = payload.get('jobs') if isinstance(payload, dict) else None
        if not isinstance(jobs, list):
            raise RecoveryError('recovery_jobs_response_invalid')
        if any(step.get('name') == UPLOAD_STEPS[phase] and step.get('status') == 'completed'
               and step.get('conclusion') == 'success' for job in jobs for step in job.get('steps', [])):
            return True
        if len(jobs) < 100:
            return False
    raise RecoveryError('recovery_job_pagination_incomplete')


def discover(token, runtime_sha, *, current_run_id=None, phases=None, excluded_artifact_ids=()):
    if not token:
        raise RecoveryError('recovery_repository_authentication_required')
    commit(runtime_sha)
    base = f'https://api.github.com/repos/{REPOSITORY}/actions'
    items = []
    for phase, prefix in PREFIXES.items():
        if phases is not None and phase not in phases:
            continue
        name = prefix + runtime_sha
        for page in range(1, 101):
            payload = api_json(f'{base}/artifacts?name={name}&per_page=100&page={page}', token)
            part = payload.get('artifacts') if isinstance(payload, dict) else None
            if not isinstance(part, list):
                raise RecoveryError('recovery_artifact_response_invalid')
            items.extend(dict(item, phase=phase) for item in part
                         if item.get('name') == name and item.get('expired') is False)
            if len(part) < 100:
                break
        else:
            raise RecoveryError('recovery_artifact_pagination_incomplete')
    for item in sorted(items, key=lambda a: (a.get('created_at', ''), int(a.get('id', 0))), reverse=True):
        if item.get('id') in excluded_artifact_ids:
            continue
        run_id = (item.get('workflow_run') or {}).get('id')
        if type(run_id) is not int or run_id <= 0:
            raise RecoveryError('recovery_source_run_id_invalid')
        if run_id == current_run_id:
            continue
        run = api_json(f'{base}/runs/{run_id}', token)
        if (run.get('head_branch') != 'main' or str(run.get('path', '')).split('@')[0] != WORKFLOW
                or run.get('event') != 'workflow_dispatch'):
            continue
        if run.get('status') != 'completed':
            raise RecoveryError('recovery_source_run_still_active')
        if run.get('conclusion') not in ('success', 'failure', 'cancelled', 'timed_out'):
            raise RecoveryError('recovery_source_run_terminal_state_unknown')
        commit(run.get('head_sha'))
        if (type(item.get('id')) is not int or item['id'] <= 0
                or item.get('archive_download_url') != f"{base}/artifacts/{item['id']}/zip"
                or not isinstance(item.get('digest'), str)
                or re.fullmatch('sha256:[0-9a-f]{64}', item['digest']) is None
                or type(item.get('size_in_bytes')) is not int or not 0 < item['size_in_bytes'] <= MAX_BYTES
                or (item.get('workflow_run') or {}).get('head_sha', run['head_sha']) != run['head_sha']):
            raise RecoveryError('recovery_artifact_identity_invalid')
        if uploaded(base, token, run_id, item['phase']):
            return dict(item, original_run=run)
    return None


def recover(*, runtime_sha, producer_sha, not_after, out, token, current_run_id=None, orphan_only=False):
    out = Path(out).resolve()
    if out.exists():
        raise RecoveryError('preserve_existing_recovery_destination')
    excluded = set()
    for _ in range(10000):
        item = discover(token, runtime_sha, current_run_id=current_run_id,
                        phases={'state'} if orphan_only else None,
                        excluded_artifact_ids=excluded)
        if item is None:
            return dict(status='no_resumable_scoped_recovery_state', restored=False)
        result = _recover_candidate(item=item, runtime_sha=runtime_sha, producer_sha=producer_sha,
                                    not_after=not_after, out=out, token=token, orphan_only=orphan_only)
        if not result.pop('scan_older_state', False):
            return result
        excluded.add(item['id'])
    raise RecoveryError('orphan_state_search_budget_exhausted')


def _recover_candidate(*, item, runtime_sha, producer_sha, not_after, out, token, orphan_only):
    if orphan_only:
        from microcap_scoped_whole_recovery import eligible_run, published_step, artifact_name
        original = item['original_run']
        base = f'https://api.github.com/repos/{REPOSITORY}/actions'
        if eligible_run(original, original['id']) and published_step(base, token, original):
            for page in range(1, 101):
                payload = api_json(f"{base}/runs/{original['id']}/artifacts?per_page=100&page={page}", token)
                values = payload.get('artifacts') if isinstance(payload, dict) else None
                if not isinstance(values, list):
                    raise RecoveryError('orphan_standard_metadata_invalid')
                for standard in values:
                    if standard.get('name') == artifact_name(runtime_sha) and standard.get('expired') is False:
                        if (re.fullmatch('sha256:[0-9a-f]{64}', str(standard.get('digest', ''))) is None
                                or (standard.get('workflow_run') or {}).get('id') != original['id']
                                or (standard.get('workflow_run') or {}).get('head_sha') != original['head_sha']):
                            raise RecoveryError('orphan_standard_identity_differs')
                        return dict(status='source_run_completed_and_standard_whole_already_published', restored=False,
                            source_run_id=original['id'], artifact_id=standard['id'], download_skipped=True,
                            verification='successful_same_run_publication_metadata_only_no_new_write_requested')
                if len(values) < 100:
                    break
            else:
                raise RecoveryError('orphan_standard_pagination_incomplete')
    outer = out.with_name(out.name + '-source-' + str(item['id']) + '.zip')
    transport = stream_download(api_request(item['archive_download_url'], token), outer,
        expected_sha=item['digest'].split(':')[1], expected_bytes=item['size_in_bytes'])
    with zipfile.ZipFile(outer) as archive:
        infos = archive.infolist(); seen = set()
        if len(infos) > MAX_FILES + 256:
            raise RecoveryError('recovery_archive_object_count_exceeded')
        for info in infos:
            name = info.filename.rstrip('/') if info.is_dir() else info.filename
            relative(name)
            if name.casefold() in seen or stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK:
                raise RecoveryError('recovery_archive_duplicate_or_link')
            seen.add(name.casefold())
        manifest_name = MANIFESTS[item['phase']]
        if archive.getinfo(manifest_name).file_size > 64 * 1024**2:
            raise RecoveryError('recovery_manifest_budget_exceeded')
        raw = archive.read(manifest_name)
        manifest = verify_manifest(json.loads(raw), runtime_sha, producer_sha, not_after)
        if (manifest['source_run_id'] != item['original_run']['id']
                or manifest['source_run_attempt'] != item['original_run'].get('run_attempt', 1)
                or manifest['producer_commit'] != item['original_run']['head_sha']
                or manifest['phase'] != item['phase']):
            raise RecoveryError('recovery_manifest_original_producer_differs')
        file_infos = {info.filename: info for info in infos if not info.is_dir()}
        if set(file_infos) != set(manifest['files']) | {manifest_name}:
            raise RecoveryError('recovery_archive_has_unbound_objects')
        if any(file_infos[name].file_size != size for name, size in manifest['sizes'].items()):
            raise RecoveryError('recovery_archive_declared_sizes_differ')
        verify_plan_binding(manifest, archive.read('scoped-recovery-plan.json'))
        if orphan_only and manifest.get('formal_state_archive') is None:
            return dict(status='partial_state_has_no_orphan_formal_archive', restored=False, scan_older_state=True)
        if shutil.disk_usage(out.parent).free < sum(manifest['sizes'].values()) + 4 * 1024**3:
            raise RecoveryError('recovery_disk_reserve_insufficient')
        out.mkdir(parents=True, exist_ok=False)
        atomic(out / manifest_name, raw)
        for name, digest in manifest['files'].items():
            target = out.joinpath(*PurePosixPath(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(name) as source, target.open('xb') as destination:
                shutil.copyfileobj(source, destination, 1024**2)
            if sha_file(target) != digest:
                raise RecoveryError('recovered_partial_object_hash_differs')
    receipt = dict(status='scoped_partial_recovery_downloaded_verified_not_adopted', restored=True,
                   manifest=str(out / manifest_name), manifest_sha256=hashlib.sha256(raw).hexdigest(),
                   source_run_id=item['original_run']['id'], source_run_attempt=manifest['source_run_attempt'],
                   producer_commit=manifest['producer_commit'], artifact_id=item['id'],
                   github_artifact_sha256=transport['sha256'], github_artifact_bytes=transport['bytes'],
                   original_target=manifest['target'], requested_not_after=not_after,
                   formal_archive=(str(out / manifest['formal_state_archive']['path']) if manifest.get('formal_state_archive') else ''),
                   native_adoption_required=True, final_strategy_delivery=False, email_sent=False, orders_placed=False)
    atomic(out / 'recovery-receipt.json', encode(receipt))
    return receipt


def orphan_publication(*, consumer, manifest_path, runtime_sha, producer_sha, not_after,
                       out, token, run_id, run_attempt, producer_commit):
    """Recover only a preserved earned archive after its checkpoint succeeded."""
    manifest_path = Path(manifest_path).resolve(); saved = manifest_path.parent
    manifest = verify_manifest(json.loads(manifest_path.read_bytes()), runtime_sha, producer_sha, not_after)
    for name, digest in manifest['files'].items():
        path = saved / name
        if not path.is_file() or sha_file(path) != digest:
            raise RecoveryError('orphan_original_object_changed')
    archive = manifest.get('formal_state_archive')
    if archive is None:
        return dict(status='no_preserved_formal_archive_in_partial_state', should_publish=False)
    out = Path(out).resolve(); out.mkdir(parents=True, exist_ok=True)
    bundle = saved / archive['path']
    prior = saved / 'scoped-delivery/whole-publication-intent.json'
    publication_result = publication(kind='whole', intent=out/'whole-publication-intent.json', token=token,
        run_id=run_id, run_attempt=run_attempt, producer_commit=producer_commit,
        bundle=bundle, runtime_sha=runtime_sha, prior_intent=prior if prior.exists() else None)
    if not publication_result['should_publish']:
        result = dict(publication_result, original_target=manifest['target'], preserved_bundle_sha256=archive['sha256'])
    else:
        from microcap_scoped_whole_recovery import restore_bundle
        work = out / 'native-archive-validation'; work.mkdir(exist_ok=True)
        validated = restore_bundle(Path(consumer), bundle, runtime_sha, not_after, work)
        if (validated['seed_date'] != manifest['target'] or validated['bundle_sha256'] != archive['sha256']
                or sha_file(bundle) != archive['sha256']):
            raise RecoveryError('orphan_native_archive_identity_differs')
        result = dict(publication_result, status='preserved_original_whole_archive_validated_without_refresh',
            original_target=manifest['target'], preserved_bundle=str(bundle),
            preserved_bundle_sha256=archive['sha256'], native_archive_validation=validated,
            new_market_data_collected=False, formal_refresh_repeated=False,
            current_signal_ready=False, email_sent=False, orders_placed=False)
    atomic(out/'orphan-publication.json', encode(result))
    return result


def _publication_jobs(run_id, token, *, attempt=None):
    base = f'https://api.github.com/repos/{REPOSITORY}/actions'
    result = []
    for page in range(1, 101):
        attempt_path = '' if attempt is None else '/attempts/' + str(attempt)
        payload = api_json(f'{base}/runs/{run_id}{attempt_path}/jobs?per_page=100&page={page}', token)
        jobs = payload.get('jobs') if isinstance(payload, dict) else None
        if not isinstance(jobs, list):
            raise RecoveryError('publication_jobs_response_invalid')
        result.extend(jobs)
        if len(jobs) < 100:
            return result
    raise RecoveryError('publication_jobs_pagination_incomplete')


def _publication_accepted(kind, run, token):
    from microcap_scoped_whole_recovery import eligible_run, published_step
    if not eligible_run(run, run['id'], allow_reconciliation=True):
        return None
    if kind == 'checkpoint':
        from restore_microcap_member_checkpoint import publication_accepted
        return publication_accepted(_publication_jobs(run['id'], token), allow_reconciliation=True)
    observation = {}
    accepted = published_step(f'https://api.github.com/repos/{REPOSITORY}/actions', token, run,
                              allow_reconciliation=True, observation=observation)
    return observation.get('kind', 'published_step_success') if accepted else None


def _publication_payload(kind, *, checkpoint=None, bundle=None, namespace_sha=None, runtime_sha=None):
    if kind == 'checkpoint':
        from restore_microcap_member_checkpoint import checkpoint_artifact_name
        checkpoint = Path(checkpoint).resolve()
        value = json.loads(checkpoint.read_bytes())
        name = checkpoint_artifact_name(checkpoint, namespace_sha)
        files, sizes = {}, {}
        for path in checkpoint.parent.rglob('*'):
            if path.is_symlink():
                raise RecoveryError('publication_checkpoint_contains_link')
            if path.is_file():
                relative_name = path.relative_to(checkpoint.parent).as_posix(); relative(relative_name)
                files[relative_name] = sha_file(path); sizes[relative_name] = path.stat().st_size
        if len(files) > 32 or sum(sizes.values()) > 64 * 1024**2 or 'checkpoint.json' not in files:
            raise RecoveryError('publication_checkpoint_payload_budget_differs')
        return name, value['as_of_date'], files, sizes, namespace_sha
    if kind != 'whole':
        raise RecoveryError('publication_kind_not_supported')
    from microcap_scoped_whole_recovery import artifact_name, bundle_identity, BUNDLE_NAME
    bundle = Path(bundle).resolve()
    with zipfile.ZipFile(bundle) as archive:
        manifest = json.loads(archive.read('outputs/top100_delivery_manifest.json'))
    target = manifest['expected_date']
    bundle_identity(bundle, runtime_sha, target)
    return artifact_name(runtime_sha), target, {BUNDLE_NAME:sha_file(bundle)}, {BUNDLE_NAME:bundle.stat().st_size}, runtime_sha


def _publication_intents(kind, namespace, target, token, directory):
    """Read the two finite pre-upload journals, including an orphan-only run."""
    base = f'https://api.github.com/repos/{REPOSITORY}/actions'
    name = 'microcap-scoped-' + kind + '-intent-v1-' + namespace
    result = []
    for page in range(1, 101):
        payload = api_json(f'{base}/artifacts?name={name}&per_page=100&page={page}', token)
        items = payload.get('artifacts') if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise RecoveryError('publication_intent_metadata_invalid')
        for item in items:
            if item.get('name') != name or item.get('expired') is not False:
                continue
            run_id = (item.get('workflow_run') or {}).get('id')
            if type(run_id) is not int or run_id <= 0:
                raise RecoveryError('publication_intent_run_invalid')
            source = api_json(f'{base}/runs/{run_id}', token)
            if (source.get('head_branch') != 'main' or source.get('event') != 'workflow_dispatch'
                    or str(source.get('path', '')).split('@')[0] != WORKFLOW):
                continue
            commit(source.get('head_sha'))
            if (type(item.get('id')) is not int or item['id'] <= 0
                    or item.get('archive_download_url') != f"{base}/artifacts/{item['id']}/zip"
                    or (item.get('workflow_run') or {}).get('head_sha') != source.get('head_sha')
                    or re.fullmatch('sha256:[0-9a-f]{64}',str(item.get('digest',''))) is None
                    or type(item.get('size_in_bytes')) is not int or not 0 < item['size_in_bytes'] <= 1024**2):
                raise RecoveryError('publication_intent_artifact_identity_differs')
            directory.mkdir(parents=True,exist_ok=True)
            archive_path=directory/('intent-artifact-'+str(item['id'])+'.zip')
            digest=item['digest'].split(':')[1]
            if not archive_path.exists():
                stream_download(api_request(item['archive_download_url'],token),archive_path,
                    expected_sha=digest,expected_bytes=item['size_in_bytes'])
            elif sha_file(archive_path)!=digest:
                raise RecoveryError('preserve_changed_publication_intent_readback')
            with zipfile.ZipFile(archive_path) as archive:
                infos=[info for info in archive.infolist() if not info.is_dir()]
                if len(infos)!=1 or infos[0].file_size>512*1024:
                    raise RecoveryError('publication_intent_payload_budget_differs')
                relative(infos[0].filename)
                raw=archive.read(infos[0]);old=json.loads(raw)
            if (old.get('schema')!='microcap_scoped_publication_intent_v1'
                    or old.get('source_run_id')!=run_id or old.get('producer_commit')!=source['head_sha']):
                raise RecoveryError('publication_intent_original_source_differs')
            if old.get('kind')==kind and old.get('namespace')==namespace and old.get('target')==target:
                path=directory/('intent-original-'+str(item['id'])+'.json')
                if path.exists() and path.read_bytes()!=raw:
                    raise RecoveryError('preserve_changed_original_publication_intent')
                if not path.exists():atomic(path,raw)
                result.append(path)
        if len(items)<100:
            return result
    raise RecoveryError('publication_intent_pagination_incomplete')


def publication(*, kind, intent, token, run_id, run_attempt, producer_commit,
                checkpoint=None, bundle=None, namespace_sha=None, runtime_sha=None, prior_intent=None):
    """Authorize one upload or reconcile its actual closed server object.

    This function performs GET only.  The existing upload-artifact step remains
    the sole writer.  An intent with a started unknown upload never authorizes
    another upload merely because a GET did not find an object.
    """
    if not token:
        raise RecoveryError('publication_repository_authentication_required')
    name, target, files, sizes, namespace = _publication_payload(kind, checkpoint=checkpoint,
        bundle=bundle, namespace_sha=namespace_sha, runtime_sha=runtime_sha)
    desired = dict(kind=kind, artifact_name=name, target=target, namespace=namespace, files=files, sizes=sizes)
    desired_sha = hashlib.sha256(encode(desired)).hexdigest()
    intent = Path(intent).resolve(); intent.parent.mkdir(parents=True, exist_ok=True)
    base = f'https://api.github.com/repos/{REPOSITORY}/actions'
    candidates = []
    for page in range(1, 101):
        payload = api_json(f'{base}/artifacts?name={name}&per_page=100&page={page}', token)
        items = payload.get('artifacts') if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise RecoveryError('publication_artifact_response_invalid')
        candidates.extend(item for item in items if item.get('name') == name and item.get('expired') is False)
        if len(items) < 100:
            break
    else:
        raise RecoveryError('publication_artifact_pagination_incomplete')
    for item in sorted(candidates, key=lambda a:int(a['id']), reverse=True):
        source_id = (item.get('workflow_run') or {}).get('id')
        if type(source_id) is not int or source_id <= 0:
            raise RecoveryError('publication_source_run_invalid')
        source = api_json(f'{base}/runs/{source_id}', token)
        # A run created before this target close could not have completed this
        # target through the unchanged independent date gate.  Avoid reading a
        # large older whole archive during every ordinary new-R publication.
        if kind == 'whole':
            created = datetime.fromisoformat(source['created_at'].replace('Z', '+00:00'))
            close = datetime.fromisoformat(target + 'T15:00:00+08:00')
            if created < close:
                continue
        accepted = _publication_accepted(kind, source, token)
        if not accepted:
            continue
        if (item.get('archive_download_url') != f"{base}/artifacts/{item['id']}/zip"
                or re.fullmatch('sha256:[0-9a-f]{64}', str(item.get('digest', ''))) is None
                or (item.get('workflow_run') or {}).get('head_sha') != source.get('head_sha')
                or type(item.get('size_in_bytes')) is not int or not 0 < item['size_in_bytes'] <= MAX_BYTES):
            raise RecoveryError('publication_server_object_identity_differs')
        checks = intent.parent / 'publication-readback'; checks.mkdir(exist_ok=True)
        outer = checks / ('artifact-' + str(item['id']) + '.zip')
        digest = item['digest'].split(':')[1]
        if not outer.exists():
            stream_download(api_request(item['archive_download_url'], token), outer,
                            expected_sha=digest, expected_bytes=item['size_in_bytes'])
        elif outer.stat().st_size != item['size_in_bytes'] or sha_file(outer) != digest:
            raise RecoveryError('preserve_changed_publication_readback')
        actual_files, actual_sizes = {}, {}
        with zipfile.ZipFile(outer) as archive:
            entries = [entry for entry in archive.infolist() if not entry.is_dir()]
            if len(entries) > (1 if kind == 'whole' else 32):
                raise RecoveryError('publication_server_payload_budget_differs')
            seen = set()
            for entry in entries:
                relative(entry.filename)
                if entry.filename.casefold() in seen or stat.S_IFMT(entry.external_attr >> 16) == stat.S_IFLNK:
                    raise RecoveryError('publication_server_payload_path_invalid')
                seen.add(entry.filename.casefold())
                hashed = hashlib.sha256(); count = 0
                with archive.open(entry) as body:
                    while chunk := body.read(1024**2):
                        count += len(chunk)
                        if count > (MAX_BYTES if kind == 'whole' else 64 * 1024**2):
                            raise RecoveryError('publication_server_payload_expanded_budget')
                        hashed.update(chunk)
                actual_files[entry.filename] = hashed.hexdigest(); actual_sizes[entry.filename] = count
        if actual_files == files and actual_sizes == sizes:
            result = dict(status='publication_server_object_already_accepted', should_publish=False,
                kind=kind, artifact_name=name, artifact_id=item['id'], source_run_id=source_id,
                producer_commit=source['head_sha'], acceptance=accepted, desired_sha256=desired_sha,
                github_artifact_sha256=digest, files=files, sizes=sizes, email_sent=False, orders_placed=False)
            atomic(intent.parent / (kind + '-publication-readback.json'), encode(result))
            return result
        if kind == 'checkpoint':
            raise RecoveryError('publication_conflicting_same_name_checkpoint')
        # Other dates intentionally share the standard whole-state name.  An
        # older archive is not the candidate and must not prevent its first
        # publication; a newer verified archive must not be shadowed by it.
        with zipfile.ZipFile(outer) as archive:
            from microcap_scoped_whole_recovery import BUNDLE_NAME
            inner = archive.open(BUNDLE_NAME)
            local = checks / ('inner-' + str(item['id']) + '.zip')
            if not local.exists():
                with inner, local.open('xb') as destination:
                    shutil.copyfileobj(inner, destination, 1024**2)
            with zipfile.ZipFile(local) as published:
                published_manifest = json.loads(published.read('outputs/top100_delivery_manifest.json'))
        if published_manifest.get('expected_date', '') > target:
            result = dict(status='publication_superseded_by_newer_whole_archive', should_publish=False,
                artifact_id=item['id'], source_run_id=source_id, target=target,
                newer_target=published_manifest['expected_date'], desired_sha256=desired_sha)
            atomic(intent.parent / (kind + '-publication-readback.json'), encode(result))
            return result
        if published_manifest.get('expected_date') == target:
            raise RecoveryError('publication_conflicting_same_target_whole_archive')
    prior_paths = list(intent.parent.glob(intent.stem + '*.json'))
    prior_paths.extend(_publication_intents(kind, namespace, target, token, intent.parent/'publication-readback'))
    if prior_intent and Path(prior_intent).is_file():
        prior_paths.append(Path(prior_intent))
    step_name = 'Publish verified member checkpoint' if kind == 'checkpoint' else 'Publish verified scoped whole state'
    for path in dict.fromkeys(prior_paths):
        old = json.loads(path.read_bytes())
        if old.get('schema') != 'microcap_scoped_publication_intent_v1':
            raise RecoveryError('publication_prior_intent_schema_differs')
        if old.get('kind') != kind or old.get('namespace') != namespace or old.get('target') != target:
            continue
        previous_id = old.get('source_run_id')
        if type(previous_id) is not int or previous_id <= 0:
            raise RecoveryError('publication_prior_intent_source_invalid')
        previous_attempt = old.get('source_run_attempt')
        if type(previous_attempt) is not int or previous_attempt <= 0:
            raise RecoveryError('publication_prior_intent_attempt_invalid')
        previous_run = api_json(f'{base}/runs/{previous_id}', token)
        if (previous_run.get('head_branch') != 'main'
                or str(previous_run.get('path', '')).split('@')[0] != WORKFLOW
                or previous_run.get('head_sha') != old.get('producer_commit')
                or previous_run.get('status') != 'completed' and previous_id != run_id):
            raise RecoveryError('publication_prior_intent_owner_unknown_or_active')
        jobs = _publication_jobs(previous_id, token, attempt=previous_attempt)
        original_steps = [step for job in jobs for step in job.get('steps', []) if step.get('name') == step_name]
        if not original_steps:
            raise RecoveryError('publication_prior_write_attempt_state_unknown')
        # An absent timestamp is not evidence that a failed/cancelled upload
        # never ran.  Only the original attempt's explicitly skipped step is
        # a closed observation permitting another write.
        if not all(step.get('status') == 'completed' and step.get('conclusion') == 'skipped'
                   for step in original_steps):
            raise RecoveryError('publication_prior_write_unknown_read_only_reconciliation_required')
    record = dict(desired, schema='microcap_scoped_publication_intent_v1', desired_sha256=desired_sha,
        source_run_id=run_id, source_run_attempt=run_attempt, producer_commit=commit(producer_commit),
        created_at=datetime.now(timezone.utc).isoformat(), status='armed_before_single_upload',
        email_sent=False, orders_placed=False)
    destination = intent if not intent.exists() else intent.with_name(intent.stem + '-run-' + str(run_id) + '.json')
    if destination.exists():
        original = json.loads(destination.read_bytes())
        if original.get('desired_sha256') != desired_sha or original.get('source_run_id') != run_id:
            raise RecoveryError('preserve_existing_publication_intent')
    else:
        atomic(destination, encode(record))
    return dict(status='publication_single_upload_authorized', should_publish=True,
                artifact_name=name, desired_sha256=desired_sha, intent=str(destination),
                intent_artifact_name='microcap-scoped-'+kind+'-intent-v1-'+namespace)


def _copy_private(source, destination, role_root):
    """Copy original bytes into an explicit private role, never rewrite JSON."""
    destination, role_root = Path(destination).absolute(), Path(role_root).absolute()
    if not destination.is_relative_to(role_root):
        raise RecoveryError('resume_admission_escapes_private_role')
    current = destination
    while current != role_root:
        if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
            raise RecoveryError('resume_admission_role_contains_link')
        current = current.parent
    if role_root.is_symlink() or (hasattr(role_root, 'is_junction') and role_root.is_junction()):
        raise RecoveryError('resume_admission_role_contains_link')
    if not destination.resolve().is_relative_to(role_root.resolve()):
        raise RecoveryError('resume_admission_escapes_private_role')
    destination.parent.mkdir(parents=True, exist_ok=True)
    expected = sha_file(source)
    if destination.is_file() and sha_file(destination) == expected:
        return
    temporary = destination.with_name(destination.name + '.admit-' + uuid.uuid4().hex)
    with Path(source).open('rb') as origin, temporary.open('xb') as target:
        shutil.copyfileobj(origin, target, 1024**2)
    if sha_file(temporary) != expected:
        raise RecoveryError('resume_admission_copy_hash_differs')
    os.replace(temporary, destination)


def _admit_resume(manifest_path, *, consumer, producer, state, prepare_out, delivery_out,
                  target, consumer_sha, producer_sha, run):
    """Admit sealed originals only after the caller verifies its formal baseline.

    An old complete archive remains a historical baseline.  Partial consumer
    state needs a same-target successful native-refresh receipt and its exact
    declared output closure, and the caller must still perform native check.
    Cross-target commands/prepared records are never copied into active output.
    """
    manifest_path = Path(manifest_path).resolve()
    saved = manifest_path.parent
    value = verify_manifest(json.loads(manifest_path.read_bytes()), consumer_sha, producer_sha, target)
    roots = [Path(p).absolute() for p in (consumer, producer, state, prepare_out, delivery_out)]
    if any(p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction()) for p in roots):
        raise RecoveryError('resume_admission_role_contains_link')
    consumer, producer, state, prepare_out, delivery_out = map(lambda p: p.resolve(), roots)
    if (consumer == producer or consumer.is_relative_to(producer) or producer.is_relative_to(consumer)
            or not producer.is_relative_to(state) or state.is_relative_to(consumer)
            or consumer.is_relative_to(state) or prepare_out.is_relative_to(consumer)
            or delivery_out.is_relative_to(consumer) or prepare_out.is_relative_to(producer)
            or delivery_out.is_relative_to(producer)):
        raise RecoveryError('resume_admission_private_roles_overlap')
    for root, expected in ((consumer, consumer_sha), (producer, producer_sha)):
        actual = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], encoding='utf-8').strip()
        if actual != expected:
            raise RecoveryError('resume_admission_exact_git_role_differs')
    for name, digest in value['files'].items():
        path = saved.joinpath(*relative(name).parts)
        if not path.is_file() or path.is_symlink() or path.stat().st_size != value['sizes'][name] or sha_file(path) != digest:
            raise RecoveryError('resume_admission_original_object_changed')
    verify_plan_binding(value, (saved / 'scoped-recovery-plan.json').read_bytes())
    roots = dict(consumer=str(consumer), producer=str(producer), state=str(state),
                 prepare_out=str(prepare_out), delivery_out=str(delivery_out))
    admission_path = delivery_out / 'resume-admission.json'
    if admission_path.exists():
        old = json.loads(admission_path.read_bytes())
        if (old.get('schema') != 'microcap_scoped_resume_admission_v1'
                or old.get('status') != 'private_originals_admitted_native_check_required'
                or old.get('manifest_sha256') != sha_file(manifest_path)
                or old.get('target') != target or old.get('roots') != roots
                or old.get('original_json_and_clocks_rewritten') is not False):
            raise RecoveryError('preserve_existing_resume_admission')
        return dict(old, admission_reused=True)
    same_target = value['target'] == target
    archive = value.get('formal_state_archive')
    consumer_partial = False
    tracked_checked = {}
    def native_digest(name, raw):
        if not name.startswith('data/st_notice_evidence/'):
            raw = raw.replace(b'\r\n', b'\n')
        return hashlib.sha256(raw).hexdigest()
    def closed_consumer_object(name, digest, *, native=False):
        sealed_name = 'scoped-consumer/' + name
        if sealed_name in value['files']:
            return (native_digest(name, (saved / sealed_name).read_bytes()) == digest if native
                    else value['files'][sealed_name] == digest)
        # Source and adjudicated evidence already supplied by this exact Git
        # checkout need not be duplicated in a partial data archive.
        if name not in tracked_checked:
            try:
                raw = subprocess.check_output(['git', '-C', str(consumer), 'show', consumer_sha + ':' + name], stderr=subprocess.PIPE)
                tracked_checked[name] = raw
            except subprocess.CalledProcessError:
                tracked_checked[name] = None
        path = consumer.joinpath(*relative(name).parts)
        raw = tracked_checked[name]
        if raw is None or not path.is_file():
            return False
        actual = path.read_bytes()
        same_git_bytes = (actual == raw if name.startswith('data/st_notice_evidence/')
                          else actual.replace(b'\r\n', b'\n') == raw.replace(b'\r\n', b'\n'))
        expected = native_digest(name, raw) if native else hashlib.sha256(raw).hexdigest()
        return same_git_bytes and expected == digest
    if not archive and same_target and value['phase'] == 'state':
        refresh_name = 'scoped-delivery/stage-receipts/refresh.json'
        context_name = 'scoped-delivery/stage-receipts/context.json'
        if refresh_name in value['files'] and context_name in value['files']:
            refresh = json.loads((saved / refresh_name).read_bytes())
            context_raw = (saved / context_name).read_bytes()
            context = json.loads(context_raw)
            if (refresh.get('schema') != 'microcap_scoped_successful_stage_v1'
                    or refresh.get('exit_code') != 0
                    or refresh.get('context_sha256') != hashlib.sha256(context_raw).hexdigest()
                    or context.get('target', context.get('delivery_date')) != target
                    or context.get('consumer_sha') != consumer_sha
                    or 'refresh-all' not in refresh.get('command', [])):
                raise RecoveryError('resume_partial_consumer_refresh_receipt_invalid')
            for obj in refresh.get('objects', []):
                role = {'consumer': 'scoped-consumer/', 'out': 'scoped-delivery/'}.get(obj.get('role'))
                if role is None:
                    raise RecoveryError('resume_refresh_object_role_unknown')
                relative(obj.get('path'))
                closed = (closed_consumer_object(obj['path'], obj.get('sha256')) if obj['role'] == 'consumer'
                          else value['files'].get(role + obj['path']) == obj.get('sha256'))
                if not closed:
                    raise RecoveryError('resume_refresh_output_is_not_closed')
            if not refresh.get('objects'):
                raise RecoveryError('resume_refresh_has_no_output_closure')
            evidence = refresh.get('command_evidence')
            if not isinstance(evidence, dict) or set(evidence) != {'stdout', 'stderr', 'process'}:
                raise RecoveryError('resume_refresh_process_evidence_missing')
            for key, item in evidence.items():
                prefix = {'consumer': 'scoped-consumer/', 'out': 'scoped-delivery/'}.get(item.get('role'))
                if prefix is None or prefix + item.get('path', '') not in value['files']:
                    raise RecoveryError('resume_refresh_process_evidence_not_closed')
            process_spec = evidence['process']
            process_name = {'consumer': 'scoped-consumer/', 'out': 'scoped-delivery/'}[process_spec['role']] + process_spec['path']
            original_process = json.loads((saved / process_name).read_bytes())
            if original_process.get('exit_code') != 0 or original_process.get('completion_unconfirmed'):
                raise RecoveryError('resume_refresh_process_not_successful')
            formal_name = 'scoped-consumer/outputs/top100_delivery_manifest.json'
            if formal_name not in value['files']:
                raise RecoveryError('resume_refresh_completed_manifest_missing')
            formal_manifest = json.loads((saved / formal_name).read_bytes())
            if (formal_manifest.get('ok') is not True or formal_manifest.get('status') != 'complete'
                    or formal_manifest.get('scope') != 'whole_workspace_delivery'
                    or formal_manifest.get('expected_date') != target
                    or any(not isinstance(formal_manifest.get(key), dict) or not formal_manifest[key]
                           for key in ('inputs', 'artifacts'))):
                raise RecoveryError('resume_refresh_completed_manifest_invalid')
            for name, digest in {**formal_manifest['inputs'], **formal_manifest['artifacts']}.items():
                relative(name)
                if not closed_consumer_object(name, digest, native=True):
                    raise RecoveryError('resume_refresh_completed_manifest_not_closed')
            consumer_partial = True
    if archive:
        run('restore-earned-formal-state', [sys.executable, '-B', consumer / 'scripts/top100_cloud_delivery.py',
             'restore', '--root', consumer, '--bundle', saved / archive['path'], '--expected-date', value['target']])
    original_manifest_restored = False
    original_manifest_name = 'scoped-delivery/formal-manifest-original.json'
    if archive and original_manifest_name in value['files']:
        original_raw = (saved / original_manifest_name).read_bytes()
        original_manifest = json.loads(original_raw)
        current_path = consumer / 'outputs/top100_delivery_manifest.json'
        packed_raw = current_path.read_bytes(); packed_manifest = json.loads(packed_raw)
        refresh_name = 'scoped-delivery/stage-receipts/refresh.json'
        if refresh_name not in value['files']:
            raise RecoveryError('formal_original_manifest_has_no_successful_refresh')
        refresh = json.loads((saved / refresh_name).read_bytes())
        originals = [item for item in refresh.get('objects', []) if item.get('role') == 'consumer'
                     and item.get('path') == 'outputs/top100_delivery_manifest.json']
        if (refresh.get('schema') != 'microcap_scoped_successful_stage_v1' or refresh.get('exit_code') != 0
                or len(originals) != 1 or originals[0].get('sha256') != hashlib.sha256(original_raw).hexdigest()):
            raise RecoveryError('formal_original_manifest_refresh_binding_differs')
        for key in ('ok', 'status', 'scope', 'expected_date', 'release_sha', 'inputs', 'artifacts', 'streams'):
            if original_manifest.get(key) != packed_manifest.get(key):
                raise RecoveryError('formal_original_manifest_economic_closure_differs')
        if (original_manifest.get('ok') is not True or original_manifest.get('status') != 'complete'
                or original_manifest.get('scope') != 'whole_workspace_delivery'
                or original_manifest.get('expected_date') != value['target']
                or original_manifest.get('release_sha') != consumer_sha):
            raise RecoveryError('formal_original_manifest_context_differs')
        for name, digest in {**original_manifest['inputs'], **original_manifest['artifacts']}.items():
            actual = consumer.joinpath(*relative(name).parts)
            if not actual.is_file() or native_digest(name, actual.read_bytes()) != digest:
                raise RecoveryError('formal_original_manifest_restored_state_differs')
        # The archive retains pack's independently inspected m2.  The candidate
        # and successful command receipts retain the original earned m1 bytes.
        # Keep both, then restore only m1 in the private consumer for reuse.
        sidecar = delivery_out / ('formal-manifest-packed-' + hashlib.sha256(packed_raw).hexdigest()[:16] + '.json')
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        if sidecar.exists() and sidecar.read_bytes() != packed_raw:
            raise RecoveryError('preserve_existing_packed_manifest_observation')
        if not sidecar.exists():
            atomic(sidecar, packed_raw)
        atomic(current_path, original_raw)
        original_manifest_restored = True
    mappings = {
        'scoped-source-state/runtime/.microcap_index_cache/': (producer / '.microcap_index_cache', 'producer'),
        'scoped-source-state/runs/': (state / 'runs', 'source_runs'),
    }
    if same_target:
        mappings.update({'scoped-prepare/': (prepare_out, 'prepare'),
                         'scoped-delivery/': (delivery_out, 'delivery')})
    else:
        mappings['scoped-prepare/boundaries/'] = (prepare_out / 'boundaries', 'dated_boundaries')
    if consumer_partial:
        mappings.update({'scoped-consumer/.microcap_index_cache/': (consumer / '.microcap_index_cache', 'consumer'),
                         'scoped-consumer/outputs/': (consumer / 'outputs', 'consumer')})
    admitted = []
    for name in value['files']:
        for prefix, (destination_root, role) in mappings.items():
            if name.startswith(prefix):
                destination = destination_root / name[len(prefix):]
                if name == 'scoped-delivery/resume-admission.json':
                    destination = delivery_out / ('prior-admission-' + value['files'][name] + '.json')
                _copy_private(saved / name, destination, destination_root)
                admitted.append(dict(original=name, destination=str(destination), role=role, sha256=value['files'][name]))
                break
        else:
            if same_target and name in STATE_FILES:
                destination = state / name[len('scoped-source-state/'):]
                _copy_private(saved / name, destination, state)
                admitted.append(dict(original=name, destination=str(destination), role='producer_bootstrap', sha256=value['files'][name]))
    path_admission = {str(Path(value['source_root']) / key.rstrip('/')):str(path) for key,(path,_) in mappings.items()}
    for logical, actual in (('scoped-consumer', consumer), ('scoped-source-state/runtime', producer),
                            ('scoped-source-state', state), ('scoped-prepare', prepare_out),
                            ('scoped-delivery', delivery_out)):
        path_admission[str(Path(value['source_root']) / logical)] = str(actual)
    prior_name = 'scoped-delivery/resume-admission.json'
    if prior_name in value['files']:
        prior = json.loads((saved / prior_name).read_bytes())
        if prior.get('schema') != 'microcap_scoped_resume_admission_v1' or prior.get('original_json_and_clocks_rewritten') is not False:
            raise RecoveryError('prior_resume_admission_invalid')
        # Preserve aliases from an earlier move as evidence rather than editing
        # older prepared JSON.  Each intermediate destination must be one of
        # this manifest's declared private roles.
        for original, intermediate in prior.get('path_admission', {}).items():
            wanted = intermediate.replace('\\', '/').rstrip('/')
            for prefix, destination in list(path_admission.items()):
                prefix = prefix.replace('\\', '/').rstrip('/')
                if wanted == prefix or wanted.startswith(prefix + '/'):
                    path_admission[original] = destination + wanted[len(prefix):]
                    break
    report = dict(schema='microcap_scoped_resume_admission_v1', status='private_originals_admitted_native_check_required',
        manifest=str(manifest_path), manifest_sha256=sha_file(manifest_path), original_target=value['target'], target=target,
        original_source_root=value['source_root'], same_target=same_target,
        roots=roots,
        formal_archive_restored=bool(archive), formal_archive_is_current_target=bool(archive and same_target),
        original_formal_manifest_restored=original_manifest_restored,
        consumer_partial_admitted=consumer_partial, native_check_required=True,
        producer_sources_admitted=any(x['role']=='producer' for x in admitted),
        original_json_and_clocks_rewritten=False, objects=admitted,
        path_admission=path_admission,
        final_strategy_delivery=False, email_sent=False, orders_placed=False)
    delivery_out.mkdir(parents=True, exist_ok=True)
    if admission_path.exists() and admission_path.read_bytes() != encode(report):
        raise RecoveryError('preserve_existing_resume_admission')
    if not admission_path.exists():
        atomic(admission_path, encode(report))
    return report


def admit_resume(manifest_path, *, consumer, producer, state, prepare_out, delivery_out,
                 target, consumer_sha, producer_sha, run):
    # A stopped parent can still have a live worker.  Probe the same OS locks
    # used by the source runner before admitting a single source byte.
    state = Path(state)
    for role in (consumer, producer, state, prepare_out, delivery_out):
        path = Path(role).absolute()
        if any(p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction()) for p in (path, *path.parents)):
            raise RecoveryError('resume_admission_role_contains_link')
    with ExitStack() as guards:
        for name in ('writer.lock', 'worker-slot-0.lock', 'worker-slot-1.lock', 'price-worker.lock'):
            guards.enter_context(writer_lock(state / name))
        return _admit_resume(manifest_path, consumer=consumer, producer=producer, state=state,
            prepare_out=prepare_out, delivery_out=delivery_out, target=target,
            consumer_sha=consumer_sha, producer_sha=producer_sha, run=run)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    make = sub.add_parser('seal')
    make.add_argument('--root', type=Path, required=True)
    make.add_argument('--consumer-sha', required=True)
    make.add_argument('--producer-sha', required=True)
    make.add_argument('--plan', type=Path, required=True)
    make.add_argument('--out', type=Path, required=True)
    make.add_argument('--phase', choices=tuple(MANIFESTS), required=True)
    read = sub.add_parser('recover')
    read.add_argument('--runtime-sha', required=True)
    read.add_argument('--producer-sha', required=True)
    read.add_argument('--not-after-date', required=True)
    read.add_argument('--out', type=Path, required=True)
    read.add_argument('--github-output', type=Path)
    read.add_argument('--orphan-only', action='store_true')
    pub = sub.add_parser('publication')
    pub.add_argument('--kind', choices=('checkpoint','whole'), required=True)
    pub.add_argument('--checkpoint', type=Path)
    pub.add_argument('--bundle', type=Path)
    pub.add_argument('--namespace-sha')
    pub.add_argument('--runtime-sha')
    pub.add_argument('--intent', type=Path, required=True)
    pub.add_argument('--prior-intent', type=Path)
    pub.add_argument('--github-output', type=Path)
    orphan = sub.add_parser('orphan')
    for name in ('consumer','manifest','out'):
        orphan.add_argument('--'+name,type=Path,required=True)
    for name in ('runtime-sha','producer-sha','not-after-date'):
        orphan.add_argument('--'+name,required=True)
    orphan.add_argument('--github-output',type=Path)
    args = parser.parse_args()
    if args.action == 'seal':
        result = seal(root=args.root, runtime_sha=args.consumer_sha, producer_sha=args.producer_sha,
            plan=args.plan, out=args.out, phase=args.phase, run_id=int(os.environ['GITHUB_RUN_ID']),
            run_attempt=int(os.environ.get('GITHUB_RUN_ATTEMPT', '1')), producer_commit=os.environ['GITHUB_SHA'])
    elif args.action == 'recover':
        result = recover(runtime_sha=args.runtime_sha, producer_sha=args.producer_sha,
            not_after=args.not_after_date, out=args.out, token=os.environ.get('GITHUB_TOKEN'),
            current_run_id=int(os.environ['GITHUB_RUN_ID']) if os.environ.get('GITHUB_RUN_ID') else None,
            orphan_only=args.orphan_only)
        if args.github_output:
            with args.github_output.open('a', encoding='utf-8') as output:
                output.write('restored=' + str(result['restored']).lower() + '\n')
                output.write('manifest=' + result.get('manifest', '') + '\n')
                output.write('formal_archive=' + result.get('formal_archive', '') + '\n')
    elif args.action == 'publication':
        result = publication(kind=args.kind, intent=args.intent, token=os.environ.get('GITHUB_TOKEN'),
            run_id=int(os.environ['GITHUB_RUN_ID']), run_attempt=int(os.environ.get('GITHUB_RUN_ATTEMPT','1')),
            producer_commit=os.environ['GITHUB_SHA'], checkpoint=args.checkpoint, bundle=args.bundle,
            namespace_sha=args.namespace_sha, runtime_sha=args.runtime_sha, prior_intent=args.prior_intent)
    else:
        result = orphan_publication(consumer=args.consumer, manifest_path=args.manifest,
            runtime_sha=args.runtime_sha, producer_sha=args.producer_sha, not_after=args.not_after_date,
            out=args.out, token=os.environ.get('GITHUB_TOKEN'), run_id=int(os.environ['GITHUB_RUN_ID']),
            run_attempt=int(os.environ.get('GITHUB_RUN_ATTEMPT','1')), producer_commit=os.environ['GITHUB_SHA'])
    if args.action in ('publication','orphan') and args.github_output:
        with args.github_output.open('a',encoding='utf-8') as output:
            output.write('should_publish='+str(result['should_publish']).lower()+'\n')
            output.write('bundle='+result.get('preserved_bundle','')+'\n')
            output.write('artifact_name='+result.get('artifact_name','')+'\n')
            output.write('intent_file='+result.get('intent','')+'\n')
            output.write('intent_artifact_name='+result.get('intent_artifact_name','')+'\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
