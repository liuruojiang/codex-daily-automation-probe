"""Read-only acceptance of exact-pin bootstrap -> independent cloud recovery.

A PASS proves archive recovery only, never current prices, mail or trading.
The CLI uses authenticated ``gh api`` reads; it does not dispatch workflows.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import zipfile
from zoneinfo import ZoneInfo

REPOSITORY = 'liuruojiang/codex-daily-automation-probe'
WORKFLOW = '.github/workflows/microcap-realtime-digest.yml'
PREFIX = 'microcap-whole-delivery-recovery-v1-'
EVIDENCE = 'microcap-recovery-check-evidence'
BUNDLE = 'microcap-whole-delivery-state.zip'
MAX_BYTES = 2 * 1024**3
RESTORE = 'Restore close-confirmed whole delivery'
FALLBACK = 'Restore approved bootstrap only when no recovery artifact exists'
REPACK = 'Repack approved restored state for the exact runtime'
REQUIRED_STEPS = (RESTORE, 'Record the exact restored archive for next-run comparison',
                  'Persist close-confirmed whole recovery state',
                  'Preserve actual recovery and three-version CSV evidence')


def integer(value):
    if type(value) is not int or value <= 0:
        raise ValueError('Positive integer identity required')
    return value


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('Missing timestamp')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('Timestamp must include timezone')
    return result.astimezone(timezone.utc)


def digest_file(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def sha(value):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise ValueError('Invalid SHA-256')
    return value


def members(archive):
    found = {}
    for item in archive.infolist():
        path = PurePosixPath(item.filename)
        if (item.orig_filename != item.filename or item.filename in found or '\\' in item.filename or path.is_absolute()
                or '..' in path.parts or ':' in item.filename or item.flag_bits & 1
                or stat.S_ISLNK(item.external_attr >> 16)):
            raise ValueError('Unsafe or duplicate ZIP member')
        found[item.filename] = item
    return found


def verify_download(path, artifact):
    if (Path(path).stat().st_size != integer(artifact.get('size_in_bytes'))
            or artifact.get('digest') != 'sha256:' + digest_file(path)):
        raise ValueError('Artifact API bytes/digest mismatch')


def inspect_archive(path, artifact, *, whole):
    """Check original wrapper bytes, then stream the actual inner archive hash."""
    verify_download(path, artifact)
    with zipfile.ZipFile(path) as archive:
        inventory = members(archive)
        files = [entry for entry in inventory.values() if not entry.is_dir()]
        if whole:
            if len(files) != 1 or files[0].filename != BUNDLE or not 0 < files[0].file_size <= MAX_BYTES:
                raise ValueError('Whole wrapper must contain exactly the native bundle')
            with archive.open(files[0]) as stream:
                bundle_sha = hashlib.file_digest(stream, 'sha256').hexdigest()
            return {'bundle_sha256': bundle_sha, 'bundle_bytes': files[0].file_size}
        if len(files) > 1000 or sum(item.file_size for item in files) > 128 * 1024**2:
            raise ValueError('Recovery evidence archive exceeds limit')
        result = {}
        for name in ('whole_state_recovery.json', 'recovery_archive_identity.json'):
            matches = [entry for entry in files if PurePosixPath(entry.filename).name == name]
            if len(matches) != 1 or matches[0].file_size > 5 * 1024**2:
                raise ValueError('Original recovery receipt missing or duplicated: ' + name)
            result[name] = json.loads(archive.read(matches[0]))
        return result


def validate_run(run, jobs, *, now):
    run_id = integer(run.get('id'))
    if (run.get('repository', {}).get('full_name') != REPOSITORY
            or run.get('status') != 'completed' or run.get('conclusion') != 'success'
            or run.get('head_branch') != 'main' or run.get('path') != WORKFLOW
            or run.get('event') != 'workflow_dispatch'
            or re.fullmatch('[0-9a-f]{40}', str(run.get('head_sha', ''))) is None):
        raise ValueError('Not a successful official main recovery workflow run')
    integer(run.get('run_attempt'))
    start, end = timestamp(run['run_started_at']), timestamp(run['updated_at'])
    if not start <= end <= now:
        raise ValueError('Run clock invalid or future')
    if not isinstance(jobs, list) or not jobs:
        raise ValueError('Actual attempt jobs required')
    for job in jobs:
        if job.get('run_id') != run_id or job.get('run_attempt') != run['run_attempt']:
            raise ValueError('Job belongs to another run/attempt')
    recovery = [job for job in jobs if job.get('name') == 'recovery-check']
    sending = [job for job in jobs if job.get('name') == 'send']
    if (len(recovery) != 1 or recovery[0].get('status') != 'completed'
            or recovery[0].get('conclusion') != 'success'
            or len(sending) != 1 or sending[0].get('conclusion') != 'skipped'):
        raise ValueError('Recovery job must succeed and send job must be skipped')
    steps = recovery[0].get('steps', [])
    def conclusion(name):
        selected = [item for item in steps if item.get('name') == name]
        if len(selected) != 1:
            raise ValueError('Actual recovery step missing or duplicated: ' + name)
        return selected[0].get('conclusion')
    for name in REQUIRED_STEPS:
        if conclusion(name) != 'success':
            raise ValueError('Required recovery step did not succeed: ' + name)
    return {'start': start, 'end': end, 'fallback': conclusion(FALLBACK),
            'repack': conclusion(REPACK)}


def validate_artifact(artifact, run, name):
    artifact_id = integer(artifact.get('id'))
    if (artifact.get('name') != name or artifact.get('expired') is not False
            or artifact.get('workflow_run', {}).get('id') != run['id']
            or artifact.get('workflow_run', {}).get('head_sha') != run['head_sha']
            or artifact.get('archive_download_url') !=
            f'https://api.github.com/repos/{REPOSITORY}/actions/artifacts/{artifact_id}/zip'
            or not 0 < integer(artifact.get('size_in_bytes')) <= MAX_BYTES
            or re.fullmatch('sha256:[0-9a-f]{64}', str(artifact.get('digest', ''))) is None):
        raise ValueError('Artifact API identity is not the exact official run artifact')
    return artifact_id


def verify_readiness(producer, consumer, strategy_sha, not_after_date, *, now=None):
    """Validate fetched original API/evidence objects and verified ZIP identities.

    Inputs are separate runs: run/jobs/artifacts/evidence and actual_bundle.
    The CLI constructs actual_bundle only by hashing the API-bound original ZIP.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or re.fullmatch('[0-9a-f]{40}', strategy_sha) is None:
        raise ValueError('Exact pin and aware current clock required')
    ceiling = date.fromisoformat(not_after_date)
    if ceiling > now.astimezone(ZoneInfo('Asia/Shanghai')).date():
        raise ValueError('Date ceiling is future')
    p, c = producer['run'], consumer['run']
    pt = validate_run(p, producer['jobs'], now=now)
    ct = validate_run(c, consumer['jobs'], now=now)
    if p['id'] == c['id'] or pt['end'] > ct['start']:
        raise ValueError('Consumer must be a later independent successful run')
    if ct['fallback'] != 'skipped' or ct['repack'] != 'skipped':
        raise ValueError('Consumer used fallback/repack rather than recovery hit')
    identities = []
    for data in (producer, consumer):
        run = data['run']
        validate_artifact(data['whole_artifact'], run, PREFIX + strategy_sha)
        validate_artifact(data['evidence_artifact'], run, EVIDENCE)
        identity = data['evidence']['recovery_archive_identity.json']
        restore = data['evidence']['whole_state_recovery.json']
        if identity.get('strategy_sha') != strategy_sha or restore.get('strategy_sha') != strategy_sha:
            raise ValueError('Recovery receipts have a different strategy pin')
        seed = date.fromisoformat(identity['seed_date'])
        if seed > ceiling:
            raise ValueError('Recovery seed is future/newer than independent session ceiling')
        identity_key = (identity['strategy_sha'], seed.isoformat(), sha(identity['bundle_sha256']),
                        integer(identity['bundle_bytes']))
        if (data['actual_bundle'] != {'bundle_sha256': identity_key[2], 'bundle_bytes': identity_key[3]}
                or identity.get('freshness') != 'verified_archive_only_requires_current_refresh'):
            raise ValueError('Actual archived bundle differs from original identity receipt')
        identities.append(identity_key)
    initial = producer['evidence']['whole_state_recovery.json']
    if initial.get('restored') is False:
        if pt['fallback'] != 'success' or pt['repack'] not in {'success', 'skipped'}:
            raise ValueError('Bootstrap did not complete its approved fallback')
    elif initial.get('restored') is True:
        if pt['fallback'] != 'skipped' or pt['repack'] != 'skipped':
            raise ValueError('Producer restore/fallback classification conflicts')
    else:
        raise ValueError('Producer restoration state is unknown')
    if identities[0] != identities[1]:
        raise ValueError('Consumer archive pin/date/bytes/SHA differs from producer')
    hit = consumer['evidence']['whole_state_recovery.json']
    source = producer['whole_artifact']
    if (hit.get('restored') is not True or integer(hit.get('source_run')) != p['id']
            or integer(hit.get('github_artifact')) != source['id']
            or hit.get('artifact_name') != source['name']
            or integer(hit.get('github_artifact_bytes')) != source['size_in_bytes']
            or 'sha256:' + sha(hit.get('github_artifact_sha256')) != source['digest']
            or any(hit.get(k) != consumer['evidence']['recovery_archive_identity.json'].get(k)
                   for k in ('strategy_sha', 'seed_date', 'bundle_sha256', 'bundle_bytes', 'freshness'))):
        raise ValueError('Consumer did not restore the exact producer artifact and bundle')
    created = timestamp(source['created_at'])
    if not pt['start'] <= created <= pt['end'] <= ct['start']:
        raise ValueError('Producer artifact chronology is invalid')
    return {'schema': 'microcap_recovery_readiness_v1', 'pass': True,
            'checked_at': now.isoformat(), 'repository': REPOSITORY,
            'strategy_sha': strategy_sha, 'producer_run': p['id'], 'consumer_run': c['id'],
            'source_artifact': source['id'], 'seed_date': identities[0][1],
            'bundle_sha256': identities[0][2], 'bundle_bytes': identities[0][3],
            'fallback_used_by_consumer': False, 'recovery_only': True,
            'current_market_authorized': False, 'email_delivery_proven': False}


def gh(endpoint, output=None):
    command = ['gh', 'api', '--hostname', 'github.com', endpoint]
    if output is None:
        result = subprocess.run(command, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1800 if output is not None else 60)
        if result.returncode:
            raise RuntimeError('Read-only GitHub API request failed')
        return json.loads(result.stdout)
    with Path(output).open('xb') as stream:
        result = subprocess.run(command, check=False, stdout=stream, stderr=subprocess.PIPE, timeout=1800 if output is not None else 60)
    if result.returncode:
        raise RuntimeError('Read-only GitHub artifact download failed')


def collect(run_id, directory, strategy_sha):
    base = f'repos/{REPOSITORY}/actions'
    run = gh(f'{base}/runs/{integer(run_id)}')
    attempt = integer(run.get('run_attempt'))
    def pages(endpoint, key):
        items = []
        for page in range(1, 101):
            payload = gh(f'{endpoint}?per_page=100&page={page}')
            batch = payload[key]
            if not isinstance(batch, list):
                raise ValueError('Invalid GitHub page')
            items.extend(batch)
            if len(batch) < 100:
                return items
        raise ValueError('GitHub pagination exceeds safety bound')
    jobs = pages(f'{base}/runs/{run_id}/attempts/{attempt}/jobs', 'jobs')
    validate_run(run, jobs, now=datetime.now(timezone.utc))
    artifacts = pages(f'{base}/runs/{run_id}/artifacts', 'artifacts')
    data = {'run': run, 'jobs': jobs, 'artifacts': artifacts}
    directory.mkdir()
    (directory / 'api.json').write_text(json.dumps(data, indent=2) + '\n', encoding='utf8')
    for role, name, whole in (('whole', PREFIX + strategy_sha, True), ('evidence', EVIDENCE, False)):
        selected = [item for item in artifacts if item.get('name') == name]
        if len(selected) != 1:
            raise ValueError('Missing/ambiguous original run artifact: ' + name)
        artifact = selected[0]
        validate_artifact(artifact, run, name)
        path = directory / (role + '.zip')
        gh(f'{base}/artifacts/{artifact["id"]}/zip', path)
        inspected = inspect_archive(path, artifact, whole=whole)
        data[role + '_artifact'] = artifact
        data['actual_bundle' if whole else 'evidence'] = inspected
    return data



def recheck(data):
    """A rerun/deletion/expiry while downloading cannot inherit an earlier PASS."""
    base = f'repos/{REPOSITORY}/actions'
    previous = data['run']
    current = gh(f"{base}/runs/{previous['id']}")
    fields = ('id', 'run_attempt', 'head_sha', 'head_branch', 'path', 'status', 'conclusion', 'updated_at')
    if any(current.get(key) != previous.get(key) for key in fields):
        raise ValueError('Run changed while collecting recovery acceptance')
    for role in ('whole_artifact', 'evidence_artifact'):
        old = data[role]
        current = gh(f"{base}/artifacts/{old['id']}")
        validate_artifact(current, previous, old['name'])
        fields = ('id', 'name', 'digest', 'size_in_bytes', 'created_at', 'archive_download_url')
        if any(current.get(key) != old.get(key) for key in fields):
            raise ValueError('Artifact changed while collecting recovery acceptance')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--producer-run', type=int, required=True)
    parser.add_argument('--consumer-run', type=int, required=True)
    parser.add_argument('--strategy-sha', required=True)
    parser.add_argument('--not-after-date', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.out.exists():
        parser.error('Output receipt must be fresh; preserve previous acceptance')
    if re.fullmatch('[0-9a-f]{40}', args.strategy_sha) is None:
        parser.error('Exact strategy SHA required')
    date.fromisoformat(args.not_after_date)
    verifier_sha = digest_file(Path(__file__))
    directory = args.out.with_suffix('.evidence')
    directory.mkdir(parents=True, exist_ok=False)
    report = {'schema': 'microcap_recovery_readiness_v1', 'pass': False,
              'recovery_only': True, 'current_market_authorized': False, 'email_delivery_proven': False}
    try:
        producer = collect(args.producer_run, directory / 'producer', args.strategy_sha)
        consumer = collect(args.consumer_run, directory / 'consumer', args.strategy_sha)
        recheck(producer)
        recheck(consumer)
        report = verify_readiness(producer, consumer, args.strategy_sha, args.not_after_date)
        if digest_file(Path(__file__)) != verifier_sha:
            raise ValueError('Verifier source changed while collecting acceptance')
        report['evidence_directory'] = str(directory.resolve())
        report['verifier_sha256'] = verifier_sha
    except Exception as exc:
        report['pass'] = False
        report['error'] = f'{type(exc).__name__}: {exc}'
    args.out.write_text(json.dumps(report, indent=2) + '\n', encoding='utf8')
    print(json.dumps(report))
    return 0 if report['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
