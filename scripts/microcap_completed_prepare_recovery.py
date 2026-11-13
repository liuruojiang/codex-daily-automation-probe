"""Recover one completed source preparation; never promote its failed consumer.

The two original artifacts and the missing, already-published warm object are
fixed by server identity and SHA.  The receiver issues a source-only manifest;
it does not manufacture a seal, rewrite the old preparation, or renew clocks.
"""
from __future__ import annotations

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
SOURCE_HEAD = '5d27efb1986837b1b66083521ecf2ac543aaa650'
SOURCE_RUN = 43241654331
SOURCE_ATTEMPT = 1
SOURCE_JOB = 131263413941
TARGET = '2026-11-12'
CONSUMER = '95d896fe58cc7cb84c0fcb7bd76534f62c9430f4'
PRODUCER = '3344e403ff01e17ef45b745a704a240e0917720b'
READER_CONSUMER = '906eca8a5dfede7fa1674fb17cd3c6ce12205e6d'
SCOPE = '0d1b049a83afe6807f1ad61d13c6cd578ccc141d9101e2005d5bad869082b2e1'
ORIGIN = 'completed_prepare_artifact_bridge_20261112_v1'
READER_SCHEMA = 'microcap_completed_prepare_reader_admission_v1'
PROVENANCE = 'scoped-prepare/receipts/completed-prepare-source.json'
SOURCE_ROOT = '/home/runner/work/codex-daily-automation-probe/codex-daily-automation-probe'
DIAGNOSTIC_PREFIX = 'codex-daily-automation-probe/codex-daily-automation-probe/'
PREPARED_SHA = 'fc5081862c4f96ec2ce7b04747e0e96549f22f1d8c1f55f5ae924747081b6732'
RESULT_SHA = '10718dcc2f3e8cb253aa1af5c123c07b64160e90d2e081d873d546795fc91e3f'
CONFIG_SHA = '7dd212eb9f3593f03df213922b405259f26adba74bda357028c58379cd5d6bae'
SOURCES_SHA = 'e5057612a00f345d831dc426df4f75f7adf6d0d95c4e9bb804b2c8926a52dda0'
PRODUCER_PROFILE_SHA = '7f74f0782d4b8eab144059d582983d9ee1e34837a50a687309a2fb286bf64072'
WARM_ID = 737853749
WARM_BYTES = 100410368
WARM_SHA = '3b327f9443105b59615338ca9e8572c07730cd64f77959a07b2af58c5cd4b3c0'
WARM_NAME = 'microcap-local-sources-close-20261111-3fe2e15e.zip'
WARM_URL = ('https://github.com/' + REPOSITORY + '/releases/download/'
            'microcap-local-sources-20261111-3fe2e15e/' + WARM_NAME)
OBJECTS = {
    'diagnostics': (13956099474, 'microcap-scoped-maintenance-rebalance_recovery', 89287220,
                    'd129f0c877217c49c4f550cb61803eb48c3b4f98182677d74f36b234409a099f'),
    'producer': (13955089800, 'microcap-scoped-producer-recovery-rebalance_recovery', 412729466,
                 'dd21238eb19cc51c60c15f83e2506e28479cf3755d58305865bca626bcc7c3cc'),
}
CONTROLLERS = {
    'maintain_microcap_local.py': '3805f256154f55db7a9f571e0996447f07cb50094138fd0b2e4ccf51018a441e',
    'microcap_local_workers.py': 'ebcbd99442e95a13e098b29ee85b28d6d2fde5ed187e1db14011ab60ff9e4675',
    'prepare_microcap_local.py': 'c0a3e9e45e5b80797a397a957a0c17f008753be796c719de84d71aa8d747f7e5',
    'microcap_local_http.py': 'ac48a9710de8f29bfb423fdd9ad2748cc349221581f6c8fc0b5b5bf2f909718f',
}
REVIEWED_CHANGES = {
    'scripts/scoped_forward_consumption.py': [
        '23c053fd49c16aa2dce8fe26ff4b6e234d695fe47ce9350b488161d48b4305f5',
        '85199f0152b08a91ec58bde363099a0670000bb118f8d921354b26b0ee02ecc6'],
    'tests/test_scoped_forward_consumption.py': [
        '52dea65d9a5aa61bb92d0d6c5926c0dfc971351dfceb2ccc68d8dbc3096a804b',
        'da37ca2870faa32c1ce131a864c8c9784a7c2c101aca7026471d0c3eea7b1e10'],
    'tests/fixtures/stock_return_basis_real/m8_turnover_a87ee0_20261110.csv.gz': [
        None, '68e2cc4983969572366bbe7429545d983b65f149cb240c632b015d21cb88fe9d'],
}


class CompletedPrepareRecoveryError(RuntimeError):
    pass


def fail(reason):
    raise CompletedPrepareRecoveryError('completed_prepare_' + reason)


def sha_file(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def reader_identity(runtime_sha, producer_sha):
    if (runtime_sha, producer_sha) != (READER_CONSUMER, PRODUCER):
        fail('exact_reader_role_differs')
    return dict(schema='microcap_completed_prepare_reader_identity_v1',
        source_consumer_sha=CONSUMER, source_producer_sha=PRODUCER,
        reader_consumer_sha=READER_CONSUMER, reader_producer_sha=PRODUCER,
        source_inputs_only=True, original_source_identity_preserved=True,
        reader_review=dict(baseline_sha=CONSUMER, runtime_sha=READER_CONSUMER,
                           changes=REVIEWED_CHANGES, data_or_parameters_changed=False))


def _producer_profile():
    raw = (Path(__file__).resolve().parents[1] / 'config/microcap_local_producer_p3_20261112.json').read_bytes()
    if hashlib.sha256(raw.replace(b'\r\n', b'\n')).hexdigest() != PRODUCER_PROFILE_SHA:
        fail('frozen_producer_profile_differs')
    profile = json.loads(raw)
    if profile.get('runtime_sha') != PRODUCER or profile.get('role') != 'source_preparation_only':
        fail('producer_profile_identity_differs')
    return profile


def _pages(url, token, key):
    for page in range(1, 101):
        value = api_json(url + f'?per_page=100&page={page}', token)
        part = value.get(key) if isinstance(value, dict) else None
        if not isinstance(part, list):
            fail('api_page_invalid')
        yield from part
        if len(part) < 100:
            return
    fail('api_pagination_incomplete')


def _artifact_identity(item, role):
    identity, name, size, digest = OBJECTS[role]
    owner = item.get('workflow_run') or {}
    if (item.get('id') != identity or item.get('name') != name or item.get('expired') is not False
            or item.get('archive_download_url') != f'https://api.github.com/repos/{REPOSITORY}/actions/artifacts/{identity}/zip'
            or owner.get('id') != SOURCE_RUN or owner.get('head_sha') != SOURCE_HEAD
            or owner.get('head_branch') != 'main' or type(item.get('size_in_bytes')) is not int
            or item['size_in_bytes'] != size or item.get('digest') != 'sha256:' + digest):
        fail('paired_artifact_identity_differs')


def discover(*, token, runtime_sha, producer_sha, not_after):
    if ((runtime_sha, producer_sha) != (READER_CONSUMER, PRODUCER)
            or date.fromisoformat(not_after).isoformat() < TARGET):
        return None
    reader_identity(runtime_sha, producer_sha)
    if not token:
        fail('repository_authentication_required')
    base = f'https://api.github.com/repos/{REPOSITORY}/actions'
    run = api_json(f'{base}/runs/{SOURCE_RUN}', token)
    if (run.get('id') != SOURCE_RUN or run.get('run_attempt') != SOURCE_ATTEMPT
            or run.get('head_sha') != SOURCE_HEAD or run.get('head_branch') != 'main'
            or run.get('event') != 'workflow_dispatch' or str(run.get('path', '')).split('@')[0] != WORKFLOW
            or run.get('status') != 'completed' or run.get('conclusion') != 'failure'
            or any((run.get(k) or {}).get('full_name') != REPOSITORY for k in ('repository', 'head_repository'))):
        fail('frozen_source_run_differs')
    jobs = list(_pages(f'{base}/runs/{SOURCE_RUN}/attempts/1/jobs', token, 'jobs'))
    scoped = [j for j in jobs if j.get('name') == 'scoped']
    if len(scoped) != 1:
        fail('scoped_attempt_missing_or_ambiguous')
    job = scoped[0]
    if (job.get('id') != SOURCE_JOB or job.get('run_id') != SOURCE_RUN
            or job.get('run_attempt') != SOURCE_ATTEMPT or job.get('head_sha') != SOURCE_HEAD
            or job.get('status') != 'completed' or job.get('conclusion') != 'failure'):
        fail('scoped_attempt_differs')
    expected = {
        'Prepare current close sources for exactly 300 candidates': 'success',
        'Earn original formal rebalance and next-session handoff': 'failure',
        'Seal resumable scoped recovery state': 'failure',
        'Preserve resumable scoped recovery state': 'skipped',
        'Preserve scoped maintenance evidence': 'success',
        'Preserve failed scoped producer sources': 'success',
        'Pack verified scoped whole state': 'skipped',
        'Publish verified member checkpoint': 'skipped',
        'Publish verified scoped whole state': 'skipped',
    }
    for name, conclusion in expected.items():
        steps = [s for s in job.get('steps', []) if s.get('name') == name]
        if len(steps) != 1 or steps[0].get('status') != 'completed' or steps[0].get('conclusion') != conclusion:
            fail('source_step_evidence_differs')
    artifacts = list(_pages(f'{base}/runs/{SOURCE_RUN}/artifacts', token, 'artifacts'))
    pair = {}
    for role, (_, name, _, _) in OBJECTS.items():
        matches = [a for a in artifacts if a.get('name') == name]
        if not matches:
            return None  # Explicit retention expiry/missing is a closed miss.
        if any(type(a.get('expired')) is not bool for a in matches):
            fail('artifact_expiry_unknown')
        live = [a for a in matches if a.get('expired') is False]
        if not live:
            return None
        if len(live) != 1:
            fail('paired_artifact_ambiguous')
        _artifact_identity(live[0], role); pair[role] = live[0]
    return dict(run_id=SOURCE_RUN, run_attempt=SOURCE_ATTEMPT, head_sha=SOURCE_HEAD,
        job_id=SOURCE_JOB, workflow_path=WORKFLOW, head_branch='main', event='workflow_dispatch',
        original_conclusion='failure', original_step_evidence=job['steps'], artifacts=pair)


def source_role(name):
    from restore_microcap_rebalance_recovery import relative
    relative(name)
    return (name.startswith(('scoped-source-state/runtime/.microcap_index_cache/',
                            f'scoped-source-state/runs/{TARGET}/', 'scoped-prepare/'))
            or name in ('scoped-source-state/bootstrap.json', 'scoped-source-state/reference-proxy.csv',
                        'scoped-schedule.json', 'scoped-recovery-plan.json'))


def _members(archive, role):
    from restore_microcap_rebalance_recovery import relative
    seen = set(); names = {}; expanded = 0
    if len(archive.infolist()) > (30000 if role == 'diagnostics' else 150000):
        fail('archive_object_budget_exceeded')
    for item in archive.infolist():
        name = item.filename.rstrip('/') if item.is_dir() else item.filename
        # Original upload included a held runner.lock. It is diagnostic only.
        relative(name[:-5] if name.endswith('.lock') else name)
        if (name.casefold() in seen or stat.S_IFMT(item.external_attr >> 16) == stat.S_IFLNK
                or item.flag_bits & 1):
            fail('archive_duplicate_link_or_encrypted')
        seen.add(name.casefold())
        expanded += item.file_size
        if expanded > (1024**3 if role == 'diagnostics' else 8 * 1024**3):
            fail('archive_expanded_budget_exceeded')
        if item.is_dir() or name.endswith('.lock'):
            continue
        if role == 'diagnostics':
            if name.startswith(DIAGNOSTIC_PREFIX):
                logical = name[len(DIAGNOSTIC_PREFIX):]
                # Retain the entire original ZIP as diagnosis. Never admit the
                # failed delivery, its context, or any partial consumer output.
                if not source_role(logical):
                    continue
            elif re.fullmatch(r'_temp/scoped-whole-recovery-[^/]+/[^/]+\.(json|log)', name):
                continue
            else:
                fail('diagnostic_workspace_prefix_differs')
        else:
            logical = name if name.startswith('scoped-source-state/') else 'scoped-source-state/' + name
            if not (logical.startswith('scoped-source-state/runtime/.microcap_index_cache/')
                    or logical in ('scoped-source-state/bootstrap.json', 'scoped-source-state/reference-proxy.csv')):
                fail('producer_role_not_allowed')
        if logical in names:
            fail('logical_alias_collision')
        names[logical] = item
    return names


def _validate_sources(out):
    from restore_microcap_rebalance_recovery import relative
    def read(name):
        return json.loads((out / name).read_bytes())
    prefix = f'scoped-source-state/runs/{TARGET}/'
    if sha_file(out / (prefix + 'result.json')) != RESULT_SHA or sha_file(out/'scoped-prepare/prepared.json') != PREPARED_SHA:
        fail('original_result_or_prepared_bytes_differ')
    result = read(prefix + 'result.json'); context = result.get('context') or {}; symbols = result.get('symbols')
    assignment = read(prefix + 'worker-context.json')
    if (result.get('status') != 'complete' or result.get('target') != TARGET
            or any(result.get(k) != 300 for k in ('total', 'processed', 'complete'))
            or result.get('failures') != {} or result.get('missing_initialized_symbols') != []
            or any(result.get(k) != 'complete' for k in ('notice_status', 'bootstrap_status', 'metadata_status', 'current_names_status'))
            or not isinstance(symbols, list) or len(symbols) != 300 or len(set(symbols)) != 300
            or any(re.fullmatch('[0-9]{6}', str(s)) is None for s in symbols)
            or assignment.get('symbols') != symbols or assignment.get('context') != context
            or context.get('target') != TARGET or context.get('runtime_sha') != PRODUCER
            or context.get('scope_sha256') != SCOPE or result.get('scope_sha256') != SCOPE
            or context.get('observation_owner_date') != TARGET
            or context.get('observation_floor') != TARGET+'T09:00:00+08:00'
            or context.get('native_sources') != _producer_profile()['calculation_sources']
            or context.get('orchestrator_sources') != CONTROLLERS
            or context.get('orchestrator_sha256') != CONTROLLERS['maintain_microcap_local.py']):
        fail('complete_source_context_or_roster_differs')
    producer = out/'scoped-source-state/runtime'; closed = {}
    keys = [s+'-'+stage for s in symbols for stage in ('price', 'share', 'ratio')] + ['notice', 'current-names', 'metadata']
    for key in keys:
        record = read(prefix + key + '.json'); files = record.get('files')
        if record.get('key') != key or record.get('context') != context or record.get('status') != 'complete' or not isinstance(files, dict) or not files:
            fail('successful_stage_context_or_closure_differs')
        if key == 'metadata' and {p for p in files if p.startswith('.microcap_index_cache/security_meta/')} != {
                '.microcap_index_cache/security_meta/'+s+'.json' for s in symbols}:
            fail('metadata_300_issuer_closure_differs')
        for name, digest in files.items():
            relative(name)
            if (not name.startswith('.microcap_index_cache/') or re.fullmatch('[0-9a-f]{64}', str(digest)) is None
                    or (name in closed and closed[name] != digest)
                    or not (producer/name).is_file() or sha_file(producer/name) != digest):
                fail('successful_source_object_missing_or_changed')
            closed[name] = digest
    bootstrap = read('scoped-source-state/bootstrap.json')
    if (bootstrap.get('status') != 'complete' or bootstrap.get('runtime_sha') not in (
            '3fe2e15ee9e3f2ee7e25bbc328479f1fd9910d36', PRODUCER)
            or bootstrap.get('source_root') != SOURCE_ROOT+'/scoped-consumer' or bootstrap.get('symbols') != symbols
            or bootstrap.get('reference_proxy_sha256') != sha_file(out/'scoped-source-state/reference-proxy.csv')
            or context.get('master_sha256') != sha_file(producer/'.microcap_index_cache/security_master.csv')):
        fail('bootstrap_or_original_master_differs')
    prepared = read('scoped-prepare/prepared.json')
    if (prepared.get('schema') != 'microcap_scoped_rebalance_prepared_v1'
            or prepared.get('status') != 'prepared_sources_only' or prepared.get('target') != TARGET
            or prepared.get('consumer_sha') != CONSUMER or prepared.get('scope_sha256') != SCOPE
            or prepared.get('issuer_count') != 300 or prepared.get('consumer_config') != 'consumer-config.json'
            or prepared.get('consumer_root') != SOURCE_ROOT+'/scoped-consumer'
            or prepared.get('producer_root') != SOURCE_ROOT+'/scoped-source-state/runtime'
            or prepared.get('files', {}).get('sources.zip') != SOURCES_SHA
            or prepared['files'].get('consumer-config.json') != CONFIG_SHA
            or prepared['files'].get('local-source-warm.zip') != WARM_SHA):
        fail('original_preparation_context_differs')
    for name, digest in prepared['files'].items():
        relative(name)
        if not (out/'scoped-prepare'/name).is_file() or sha_file(out/'scoped-prepare'/name) != digest:
            fail('original_preparation_object_missing_or_changed')
    from microcap_scoped_inputs import read_bundle
    manifest, bodies, _ = read_bundle(out/'scoped-prepare/sources.zip', TARGET, SOURCES_SHA)
    if (manifest.get('runtime_sha') != PRODUCER or manifest.get('target') != TARGET
            or manifest.get('symbols') != symbols or manifest.get('scope_sha256') != SCOPE
            or manifest.get('producer_context') != context
            or any(closed.get(name) != digest or sha_file(producer/name) != digest
                   for name, digest in manifest['files'].items())):
        fail('original_source_bundle_context_differs')
    del bodies
    commands = list((out/'scoped-prepare/commands').glob('maintain-*/process.json'))
    processes = [json.loads(p.read_bytes()) for p in commands]
    successful = [p for p in processes if p.get('exit_code') == 0 and not p.get('completion_unconfirmed')]
    if len(successful) != 1:
        fail('successful_original_maintain_process_missing_or_ambiguous')
    argv = successful[0].get('command') or []
    if (not isinstance(argv, list) or argv.count('--as-of') != 1 or argv[-1:] == ['--as-of']
            or argv[argv.index('--as-of')+1] != TARGET
            or SOURCE_ROOT+'/automation/scripts/maintain_microcap_local.py' not in argv):
        fail('successful_original_maintain_command_differs')
    return dict(successful_stock_stages=900, successful_auxiliary_stages=3, successful_stages=903,
        complete_issuers=300, failed_stages=0, scope_sha256=SCOPE,
        source_result_sha256=RESULT_SHA, original_context_sha256=hashlib.sha256(encode(context)).hexdigest(),
        unique_closed_source_objects=len(closed), original_observation_floor=context['observation_floor'])


def _validate_provenance(value):
    if (value.get('schema') != 'microcap_completed_prepare_provenance_v1' or value.get('profile') != ORIGIN
            or value.get('source_run_id') != SOURCE_RUN or value.get('source_run_attempt') != SOURCE_ATTEMPT
            or value.get('source_auto_sha') != SOURCE_HEAD or value.get('source_consumer_sha') != CONSUMER
            or value.get('source_producer_sha') != PRODUCER or value.get('target') != TARGET
            or value.get('scope_sha256') != SCOPE or value.get('prepared_sha256') != PREPARED_SHA
            or value.get('consumer_config_sha256') != CONFIG_SHA or value.get('sources_zip_sha256') != SOURCES_SHA
            or value.get('original_json_and_clocks_rewritten') is not False
            or value.get('consumer_partial_admitted') is not False or value.get('formal_state_admitted') is not False
            or value.get('native_source_import_required') is not True):
        fail('source_provenance_differs')
    summary = value.get('summary') or {}
    if any(summary.get(k) != v for k, v in dict(successful_stock_stages=900, successful_auxiliary_stages=3,
            successful_stages=903, complete_issuers=300, failed_stages=0, source_result_sha256=RESULT_SHA,
            scope_sha256=SCOPE, original_observation_floor=TARGET+'T09:00:00+08:00').items()):
        fail('complete_source_summary_differs')
    evidence = value.get('source_preparation_evidence') or {}
    if (evidence.get('run_id') != SOURCE_RUN or evidence.get('run_attempt') != SOURCE_ATTEMPT
            or evidence.get('head_sha') != SOURCE_HEAD or evidence.get('job_id') != SOURCE_JOB):
        fail('source_provenance_run_differs')
    for role in OBJECTS:
        _artifact_identity((evidence.get('artifacts') or {}).get(role) or {}, role)
    warm = value.get('original_missing_warm_object') or {}
    if warm != dict(asset_id=WARM_ID, sha256=WARM_SHA, bytes=WARM_BYTES, url=WARM_URL):
        fail('original_warm_object_identity_differs')
    return value


def validate_bridge_manifest(value):
    if (value.get('manifest_origin') != ORIGIN or value.get('source_run_id') != SOURCE_RUN
            or value.get('source_run_attempt') != SOURCE_ATTEMPT or value.get('producer_commit') != SOURCE_HEAD
            or value.get('runtime_sha') != CONSUMER or value.get('producer_sha') != PRODUCER
            or value.get('target') != TARGET or value.get('source_root') != SOURCE_ROOT
            or value.get('phase') != 'state' or value.get('source_inputs_only') is not True
            or value.get('original_source_was_sealed') is not False or 'sealed_at' in value
            or value.get('formal_state_archive') is not None or value.get('required_rebalance_dates') != [TARGET]
            or PROVENANCE not in value.get('files', {})
            or value['files'].get('scoped-prepare/prepared.json') != PREPARED_SHA
            or value['files'].get('scoped-prepare/consumer-config.json') != CONFIG_SHA
            or value['files'].get('scoped-prepare/sources.zip') != SOURCES_SHA
            or any(not source_role(name) for name in value['files'])):
        fail('manifest_source_only_context_differs')
    issuer = value.get('manifest_issuer') or {}
    if (issuer.get('role') != 'current_receiver_bridge' or issuer.get('head_sha') == SOURCE_HEAD
            or re.fullmatch('[0-9a-f]{40}', str(issuer.get('head_sha', ''))) is None):
        fail('manifest_receiver_identity_differs')
    for role in OBJECTS:
        _artifact_identity((value.get('source_preparation_evidence', {}).get('artifacts') or {}).get(role) or {}, role)


def _reader_checkout(consumer, producer):
    def git(root, *argv):
        return subprocess.check_output(['git', '-C', str(root), *argv], stderr=subprocess.PIPE)
    for root, expected in ((consumer, READER_CONSUMER), (producer, PRODUCER)):
        if git(root, 'rev-parse', 'HEAD').decode().strip() != expected:
            fail('actual_reader_checkout_differs')
    changed = {p.decode() for p in git(consumer, 'diff', '--no-renames', '--name-only', '-z', CONSUMER, READER_CONSUMER).split(b'\0') if p}
    if changed != set(REVIEWED_CHANGES):
        fail('reader_complete_reviewed_diff_differs')
    for name, pair in REVIEWED_CHANGES.items():
        for revision, expected in zip((CONSUMER, READER_CONSUMER), pair):
            entry = git(consumer, 'ls-tree', '-z', revision, '--', name)
            if expected is None:
                if entry:
                    fail('reader_added_source_was_present')
            else:
                if not entry or entry.split(b' ', 2)[0] not in (b'100644', b'100755'):
                    fail('reader_reviewed_source_not_regular')
                raw = git(consumer, 'show', revision+':'+name)
                if hashlib.sha256(raw).hexdigest() != expected:
                    fail('reader_reviewed_git_source_differs')
        actual = (Path(consumer)/name).read_bytes()
        raw = git(consumer, 'show', READER_CONSUMER+':'+name)
        # Compressed fixture is binary; Python source follows native LF policy.
        if (actual if name.endswith('.gz') else actual.replace(b'\r\n', b'\n')) != raw:
            fail('reader_working_source_differs')
    for name, expected in _producer_profile()['calculation_sources'].items():
        if hashlib.sha256((Path(producer)/name).read_bytes().replace(b'\r\n', b'\n')).hexdigest() != expected:
            fail('producer_native_sources_changed')


def prepared_reader_admission(manifest_path, value, *, prepare_out, consumer, producer, target):
    """Reissue only the reader admission; every old producer object stays raw."""
    from restore_microcap_rebalance_recovery import relative
    manifest_path = Path(manifest_path); prepare_out = Path(prepare_out)
    if target != TARGET or PROVENANCE not in value.get('files', {}):
        return None
    provenance_path = prepare_out/'receipts/completed-prepare-source.json'
    provenance = _validate_provenance(json.loads(provenance_path.read_bytes()))
    if sha_file(provenance_path) != value['files'][PROVENANCE]:
        fail('admitted_provenance_hash_differs')
    for name, wanted in (('prepared.json', PREPARED_SHA), ('consumer-config.json', CONFIG_SHA), ('sources.zip', SOURCES_SHA)):
        relative(name)
        if value['files'].get('scoped-prepare/'+name) != wanted or sha_file(prepare_out/name) != wanted:
            fail('admitted_preparation_hash_differs')
    _reader_checkout(Path(consumer), Path(producer))
    return dict(provenance, schema=READER_SCHEMA,
        reader_consumer_sha=READER_CONSUMER, reader_producer_sha=PRODUCER,
        reader_review=reader_identity(READER_CONSUMER, PRODUCER)['reader_review'],
        manifest_sha256=sha_file(manifest_path), provenance_path=str(provenance_path),
        provenance_sha256=value['files'][PROVENANCE])


def validate_prepared_reader_admission(admission, *, prepared, config, consumer, producer, target, consumer_sha):
    if not isinstance(admission, dict) or target != TARGET:
        fail('prepared_reader_admission_required')
    reader_identity(consumer_sha, PRODUCER)
    value = admission.get('prepared_source_reader')
    if not isinstance(value, dict) or value.get('schema') != READER_SCHEMA:
        fail('prepared_reader_admission_required')
    _validate_provenance(dict(value, schema='microcap_completed_prepare_provenance_v1'))
    prepared, config = Path(prepared).resolve(), Path(config).resolve()
    provenance_path = prepared.parent/'receipts/completed-prepare-source.json'
    if (value.get('manifest_sha256') != admission.get('manifest_sha256')
            or value.get('reader_consumer_sha') != consumer_sha or value.get('reader_producer_sha') != PRODUCER
            or value.get('reader_review') != reader_identity(consumer_sha, PRODUCER)['reader_review']
            or value.get('provenance_path') != str(provenance_path)
            or sha_file(prepared) != PREPARED_SHA or sha_file(config) != CONFIG_SHA
            or sha_file(prepared.parent/'sources.zip') != SOURCES_SHA
            or sha_file(provenance_path) != value.get('provenance_sha256')
            or config != prepared.parent/'consumer-config.json'
            or admission.get('roots', {}).get('consumer') != str(Path(consumer).resolve())
            or admission.get('roots', {}).get('producer') != str(Path(producer).resolve())
            or admission.get('original_json_and_clocks_rewritten') is not False):
        fail('prepared_reader_hash_role_or_manifest_differs')
    closed = [obj for obj in admission.get('objects', []) if obj.get('original') == PROVENANCE
              and obj.get('destination') == str(provenance_path) and obj.get('sha256') == value['provenance_sha256']]
    if len(closed) != 1:
        fail('prepared_reader_provenance_not_closed')
    actual = _validate_provenance(json.loads(provenance_path.read_bytes()))
    for key, item in actual.items():
        if key != 'schema' and value.get(key) != item:
            fail('prepared_reader_provenance_content_differs')
    _reader_checkout(Path(consumer), Path(producer))
    return value


def _warm_metadata(token):
    url = f'https://api.github.com/repos/{REPOSITORY}/releases/assets/{WARM_ID}'
    warm = api_json(url, token)
    if (warm.get('id') != WARM_ID or warm.get('url') != url or warm.get('name') != WARM_NAME
            or warm.get('state') != 'uploaded' or warm.get('digest') != 'sha256:'+WARM_SHA
            or type(warm.get('size')) is not int or warm['size'] != WARM_BYTES
            or warm.get('browser_download_url') != WARM_URL):
        fail('missing_warm_server_identity_differs')
    return warm


def recover(*, token, runtime_sha, producer_sha, not_after, out, issuer_head=None):
    from restore_microcap_rebalance_recovery import (MANIFESTS, SCHEMA, STATE_BUDGET_PROFILE,
        object_role_counts, verify_manifest, verify_plan_binding)
    out = Path(out).resolve()
    if out.exists():
        fail('preserve_existing_recovery_destination')
    source = discover(token=token, runtime_sha=runtime_sha, producer_sha=producer_sha, not_after=not_after)
    if source is None:
        return dict(restored=False, status='no_frozen_completed_prepare_source_pair')
    warm = _warm_metadata(token)  # Close all small server identities before big GETs.
    archives = {}
    for role, item in source['artifacts'].items():
        archive = out.with_name(out.name+'-completed-prepare-'+role+'-'+str(item['id'])+'.zip')
        stream_download(api_request(item['archive_download_url'], token), archive,
            expected_sha=item['digest'].split(':')[1], expected_bytes=item['size_in_bytes'])
        if archive.stat().st_size != item['size_in_bytes'] or sha_file(archive) != item['digest'].split(':')[1]:
            fail('downloaded_original_outer_identity_differs')
        archives[role] = archive
    with zipfile.ZipFile(archives['diagnostics']) as diagnostic, zipfile.ZipFile(archives['producer']) as producer:
        mappings = {'diagnostics': _members(diagnostic, 'diagnostics'), 'producer': _members(producer, 'producer')}
        expanded = sum(info.file_size for mapping in mappings.values() for info in mapping.values()) + WARM_BYTES
        if shutil.disk_usage(out.parent).free < expanded + 4*1024**3:
            fail('recovery_disk_reserve_insufficient')
        out.mkdir(parents=True, exist_ok=False)
        for role, archive in (('diagnostics', diagnostic), ('producer', producer)):
            for logical, item in mappings[role].items():
                path = out/logical; path.parent.mkdir(parents=True, exist_ok=True)
                if path.exists():
                    with archive.open(item) as raw:
                        if path.read_bytes() != raw.read():
                            fail('paired_original_object_conflict')
                    continue
                with archive.open(item) as raw, path.open('xb') as destination:
                    shutil.copyfileobj(raw, destination, 1024**2)
                if path.stat().st_size != item.file_size:
                    fail('original_object_incomplete')
    # This object was in prepared.files but omitted by the failed run's upload
    # path. Restore the existing exact published bytes, never edit the receipt.
    warm_path = out/'scoped-prepare/local-source-warm.zip'
    if not warm_path.exists():
        request = api_request(warm['url'], token)
        request.add_header('Accept', 'application/octet-stream')
        stream_download(request, warm_path,
                        expected_sha=WARM_SHA, expected_bytes=WARM_BYTES)
    if warm_path.stat().st_size != WARM_BYTES or sha_file(warm_path) != WARM_SHA:
        fail('missing_warm_original_bytes_differ')
    summary = _validate_sources(out)
    issuer_head = issuer_head or os.environ.get('GITHUB_SHA') or subprocess.check_output(
        ['git', '-C', str(Path(__file__).resolve().parents[1]), 'rev-parse', 'HEAD'], encoding='utf-8').strip()
    if re.fullmatch('[0-9a-f]{40}', issuer_head) is None or issuer_head == SOURCE_HEAD:
        fail('receiver_commit_required')
    provenance = dict(schema='microcap_completed_prepare_provenance_v1', profile=ORIGIN,
        source_run_id=SOURCE_RUN, source_run_attempt=SOURCE_ATTEMPT, source_auto_sha=SOURCE_HEAD,
        source_consumer_sha=CONSUMER, source_producer_sha=PRODUCER, target=TARGET, scope_sha256=SCOPE,
        prepared_sha256=PREPARED_SHA, consumer_config_sha256=CONFIG_SHA, sources_zip_sha256=SOURCES_SHA,
        source_preparation_evidence=source, summary=summary,
        original_missing_warm_object=dict(asset_id=WARM_ID, sha256=WARM_SHA, bytes=WARM_BYTES, url=WARM_URL),
        original_json_and_clocks_rewritten=False, consumer_partial_admitted=False,
        formal_state_admitted=False, native_source_import_required=True)
    _validate_provenance(provenance)
    if (out/PROVENANCE).exists():
        fail('preserve_existing_source_provenance')
    atomic(out/PROVENANCE, encode(provenance))
    # The original real plan is available in diagnostics; keep it byte-for-byte.
    files = {}; sizes = {}
    for path in sorted(out.rglob('*')):
        if path.is_file():
            name = path.relative_to(out).as_posix()
            if not source_role(name) or path.is_symlink():
                fail('receiver_selected_non_source_role')
            files[name] = sha_file(path); sizes[name] = path.stat().st_size
    manifest = dict(schema=SCHEMA, status='resumable_partial', phase='state',
        manifest_origin=ORIGIN, original_source_was_sealed=False, source_inputs_only=True,
        manifest_issuer=dict(role='current_receiver_bridge', head_sha=issuer_head),
        bridged_at=datetime.now(timezone.utc).isoformat(), source_preparation_evidence=source,
        runtime_sha=CONSUMER, producer_sha=PRODUCER, producer_commit=SOURCE_HEAD,
        source_run_id=SOURCE_RUN, source_run_attempt=SOURCE_ATTEMPT, source_root=SOURCE_ROOT,
        target=TARGET, required_rebalance_dates=[TARGET], plan_sha256=files['scoped-recovery-plan.json'],
        files=files, sizes=sizes, object_budget_profile=STATE_BUDGET_PROFILE, object_role_counts=object_role_counts(files),
        formal_state_archive=None, native_adoption_required=True, final_strategy_delivery=False, email_sent=False, orders_placed=False)
    verify_manifest(manifest, CONSUMER, PRODUCER, not_after)
    verify_plan_binding(manifest, (out/'scoped-recovery-plan.json').read_bytes())
    manifest_path = out/MANIFESTS['state']; atomic(manifest_path, encode(manifest))
    receipt = dict(status='completed_prepare_sources_downloaded_verified_not_adopted', restored=True,
        manifest=str(manifest_path), manifest_sha256=sha_file(manifest_path), manifest_origin=ORIGIN,
        source_run_id=SOURCE_RUN, source_run_attempt=SOURCE_ATTEMPT, producer_commit=SOURCE_HEAD,
        original_target=TARGET, requested_not_after=not_after, formal_archive='', summary=summary,
        source_preparation_evidence=source, validation_reader=reader_identity(runtime_sha, producer_sha),
        original_json_and_clocks_rewritten=False, native_adoption_required=True,
        final_strategy_delivery=False, email_sent=False, orders_placed=False)
    atomic(out/'recovery-receipt.json', encode(receipt))
    return receipt
