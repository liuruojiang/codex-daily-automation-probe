"""Offline control-plane cases; synthetic APIs are not real cloud acceptance."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import zipfile

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/verify_microcap_recovery_readiness.py'
spec = importlib.util.spec_from_file_location('recovery_readiness_test', SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
PIN = 'a' * 40
NOW = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)


def artifact(run, name, artifact_id):
    return {'id': artifact_id, 'name': name, 'expired': False, 'size_in_bytes': 500,
            'digest': 'sha256:' + 'b' * 64, 'created_at': run['run_started_at'],
            'workflow_run': {'id': run['id'], 'head_sha': run['head_sha']},
            'archive_download_url': f'https://api.github.com/repos/{m.REPOSITORY}/actions/artifacts/{artifact_id}/zip'}


def data(run_id, hour):
    run = dict(id=run_id, repository={'full_name': m.REPOSITORY}, status='completed',
               conclusion='success', head_branch='main', path=m.WORKFLOW,
               event='workflow_dispatch', head_sha='c' * 40, run_attempt=1,
               run_started_at=f'2026-11-05T{hour:02d}:00:00Z',
               updated_at=f'2026-11-05T{hour:02d}:30:00Z')
    steps = [{'name': name, 'conclusion': 'success'} for name in m.REQUIRED_STEPS]
    steps += [{'name': name, 'conclusion': 'skipped'} for name in (m.FALLBACK, m.REPACK)]
    jobs = [dict(name='recovery-check', run_id=run_id, run_attempt=1, status='completed',
                 conclusion='success', steps=steps),
            dict(name='send', run_id=run_id, run_attempt=1, conclusion='skipped')]
    identity = dict(strategy_sha=PIN, seed_date='2026-11-04', bundle_sha256='d' * 64,
                    bundle_bytes=1000, freshness='verified_archive_only_requires_current_refresh')
    return {'run': run, 'jobs': jobs, 'whole_artifact': artifact(run, m.PREFIX + PIN, run_id * 10),
            'evidence_artifact': artifact(run, m.EVIDENCE, run_id * 10 + 1),
            'evidence': {'whole_state_recovery.json': {'restored': False, 'strategy_sha': PIN},
                         'recovery_archive_identity.json': identity},
            'actual_bundle': {k: identity[k] for k in ('bundle_sha256', 'bundle_bytes')}}


@pytest.fixture
def pair():
    producer, consumer = data(11, 8), data(22, 9)
    producer['jobs'][0]['steps'][-2]['conclusion'] = 'success'
    producer['jobs'][0]['steps'][-1]['conclusion'] = 'success'
    source = producer['whole_artifact']
    consumer['evidence']['whole_state_recovery.json'] = {
        **consumer['evidence']['recovery_archive_identity.json'], 'restored': True,
        'source_run': 11, 'github_artifact': source['id'], 'artifact_name': source['name'],
        'github_artifact_bytes': source['size_in_bytes'], 'github_artifact_sha256': 'b' * 64}
    return producer, consumer


def check(pair):
    return m.verify_readiness(*pair, PIN, '2026-11-05', now=NOW)


def setpath(obj, path, value):
    for part in path[:-1]:
        obj = obj[part]
    obj[path[-1]] = value


def test_two_independent_exact_pin_runs_are_archive_only(pair):
    report = check(pair)
    assert report['pass'] is True and report['source_artifact'] == 110
    assert report['recovery_only'] is True
    assert report['current_market_authorized'] is report['email_delivery_proven'] is False


@pytest.mark.parametrize('which,path,value', [
    (0, ('run', 'conclusion'), 'failure'),
    (1, ('run', 'conclusion'), 'cancelled'),
    (1, ('run', 'head_branch'), 'candidate'),
    (0, ('run', 'repository', 'full_name'), 'liuruojiang/microcap'),
    (1, ('run', 'path'), '.github/workflows/other.yml'),
    (1, ('run', 'event'), 'schedule'),
    (1, ('run', 'updated_at'), '2026-11-06T09:31:00Z'),
    (1, ('run', 'run_started_at'), '2026-11-05T08:01:00Z'),
    (1, ('jobs', 0, 'run_attempt'), 2),
    (1, ('jobs', 1, 'conclusion'), 'success'),
    (1, ('jobs', 0, 'steps', 0, 'conclusion'), 'skipped'),
    (1, ('jobs', 0, 'steps', -2, 'conclusion'), 'success'),
    (1, ('jobs', 0, 'steps', -1, 'conclusion'), 'success'),
    (0, ('jobs', 0, 'steps', -2, 'conclusion'), 'skipped'),
    (0, ('evidence', 'whole_state_recovery.json', 'restored'), None),
    (1, ('evidence', 'whole_state_recovery.json', 'restored'), 'true'),
    (1, ('evidence', 'whole_state_recovery.json', 'source_run'), 33),
    (1, ('evidence', 'whole_state_recovery.json', 'github_artifact'), 999),
    (1, ('evidence', 'whole_state_recovery.json', 'github_artifact_bytes'), 501),
    (1, ('evidence', 'whole_state_recovery.json', 'github_artifact_sha256'), 'f' * 64),
    (1, ('evidence', 'whole_state_recovery.json', 'artifact_name'), m.PREFIX + 'f' * 40),
    (1, ('evidence', 'recovery_archive_identity.json', 'strategy_sha'), 'f' * 40),
    (1, ('evidence', 'recovery_archive_identity.json', 'seed_date'), '2026-11-06'),
    (1, ('evidence', 'recovery_archive_identity.json', 'bundle_bytes'), True),
    (1, ('evidence', 'recovery_archive_identity.json', 'bundle_sha256'), 'f' * 64),
    (1, ('actual_bundle', 'bundle_sha256'), 'f' * 64),
    (0, ('whole_artifact', 'expired'), True),
    (0, ('whole_artifact', 'archive_download_url'), 'https://example.com/zip'),
    (0, ('whole_artifact', 'workflow_run', 'id'), 999),
    (0, ('whole_artifact', 'workflow_run', 'head_sha'), 'f' * 40),
    (0, ('whole_artifact', 'created_at'), '2026-11-05T09:01:00Z'),
    (0, ('evidence_artifact', 'digest'), ''),
])
def test_unqualified_or_contradictory_evidence_rejected(pair, which, path, value):
    setpath(pair[which], path, value)
    with pytest.raises((ValueError, KeyError)):
        check(pair)


def test_same_run_cannot_be_its_own_consumer(pair):
    pair = pair[0], copy.deepcopy(pair[0])
    with pytest.raises(ValueError, match='independent'):
        check(pair)


def test_step_ambiguity_rejected(pair):
    pair[1]['jobs'][0]['steps'].append(dict(pair[1]['jobs'][0]['steps'][0]))
    with pytest.raises(ValueError, match='duplicated'):
        check(pair)


def wrapper(tmp_path, content):
    path = tmp_path / 'original.zip'
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, raw in content:
            entry = zipfile.ZipInfo(name)
            entry.filename = name  # Keep the raw hostile spelling on Windows.
            archive.writestr(entry, raw)
    api = {'size_in_bytes': path.stat().st_size, 'digest': 'sha256:' + m.digest_file(path)}
    return path, api


def test_actual_wrapper_api_and_inner_bytes(tmp_path):
    raw = b'actual offline bundle bytes'
    path, api = wrapper(tmp_path, [(m.BUNDLE, raw)])
    assert m.inspect_archive(path, api, whole=True) == {
        'bundle_sha256': hashlib.sha256(raw).hexdigest(), 'bundle_bytes': len(raw)}
    api['size_in_bytes'] += 1
    with pytest.raises(ValueError, match='API'):
        m.inspect_archive(path, api, whole=True)


def test_api_digest_mismatch_rejected(tmp_path):
    path, api = wrapper(tmp_path, [(m.BUNDLE, b'bytes')])
    api['digest'] = 'sha256:' + '0' * 64
    with pytest.raises(ValueError, match='digest'):
        m.inspect_archive(path, api, whole=True)


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'C:/drive', 'bad\\name'])
def test_wrapper_unsafe_member_rejected(tmp_path, name):
    path, api = wrapper(tmp_path, [(name, b'bad')])
    with pytest.raises(ValueError, match='ZIP'):
        m.inspect_archive(path, api, whole=True)


def test_evidence_missing_duplicate_receipt_rejected(tmp_path):
    path, api = wrapper(tmp_path, [('a/whole_state_recovery.json', b'{}'),
                                  ('b/whole_state_recovery.json', b'{}'),
                                  ('recovery_archive_identity.json', b'{}')])
    with pytest.raises(ValueError, match='duplicated'):
        m.inspect_archive(path, api, whole=False)


def test_original_evidence_receipts_read_without_rewriting(tmp_path, pair):
    expected = pair[1]['evidence']
    path, api = wrapper(tmp_path, [(name, json.dumps(value).encode()) for name, value in expected.items()])
    original = path.read_bytes()
    assert m.inspect_archive(path, api, whole=False) == expected
    assert path.read_bytes() == original


def test_cli_failure_saved_no_dispatch_and_no_existing_receipt_overwrite(tmp_path, monkeypatch):
    calls = []
    def fail(*args):
        calls.append(args)
        raise RuntimeError('Read-only GitHub API request failed')
    monkeypatch.setattr(m, 'collect', fail)
    out = tmp_path / 'acceptance.json'
    args = ['--producer-run', '11', '--consumer-run', '22', '--strategy-sha', PIN,
            '--not-after-date', '2026-11-05', '--out', str(out)]
    assert m.main(args) == 1
    receipt = json.loads(out.read_bytes())
    assert receipt['pass'] is False and len(calls) == 1
    original = out.read_bytes()
    with pytest.raises(SystemExit):
        m.main(args)
    assert out.read_bytes() == original


def test_collect_downloads_only_bound_official_api_artifacts(tmp_path, monkeypatch, pair):
    original = pair[0]
    bundle = b'offline control bundle'
    identity = original['evidence']['recovery_archive_identity.json']
    identity.update(bundle_sha256=hashlib.sha256(bundle).hexdigest(), bundle_bytes=len(bundle))
    state_path, _ = wrapper(tmp_path, [(m.BUNDLE, bundle)])
    state_raw = state_path.read_bytes()
    state_path.unlink()
    evidence_path, _ = wrapper(tmp_path, [(k, json.dumps(v).encode()) for k, v in original['evidence'].items()])
    evidence_raw = evidence_path.read_bytes()
    for artifact, raw in ((original['whole_artifact'], state_raw), (original['evidence_artifact'], evidence_raw)):
        artifact.update(size_in_bytes=len(raw), digest='sha256:' + hashlib.sha256(raw).hexdigest())
    calls = []
    def fetch(endpoint, output=None):
        calls.append(endpoint)
        assert endpoint.startswith('repos/' + m.REPOSITORY + '/actions/')
        assert 'dispatch' not in endpoint
        if endpoint.endswith('/runs/11'):
            return original['run']
        if '/attempts/1/jobs?' in endpoint:
            return {'jobs': original['jobs']}
        if '/runs/11/artifacts?' in endpoint:
            return {'artifacts': [original['whole_artifact'], original['evidence_artifact']]}
        if endpoint.endswith('/artifacts/110/zip'):
            Path(output).write_bytes(state_raw)
        elif endpoint.endswith('/artifacts/111/zip'):
            Path(output).write_bytes(evidence_raw)
        else:
            raise AssertionError(endpoint)
    monkeypatch.setattr(m, 'gh', fetch)
    actual = m.collect(11, tmp_path / 'fetched', PIN)
    assert actual['evidence'] == original['evidence']
    assert actual['actual_bundle'] == {'bundle_sha256': identity['bundle_sha256'], 'bundle_bytes': len(bundle)}
    assert len(calls) == 5


@pytest.mark.parametrize('mutation', ['run_attempt', 'artifact_digest', 'expired'])
def test_end_recheck_rejects_rerun_or_artifact_change(pair, monkeypatch, mutation):
    original = pair[0]
    def fetch(endpoint):
        if '/runs/' in endpoint:
            value = copy.deepcopy(original['run'])
            if mutation == 'run_attempt':
                value['run_attempt'] += 1
            return value
        artifact_id = int(endpoint.rsplit('/', 1)[-1])
        value = copy.deepcopy(next(original[key] for key in ('whole_artifact', 'evidence_artifact')
                                   if original[key]['id'] == artifact_id))
        if mutation == 'artifact_digest':
            value['digest'] = 'sha256:' + 'f' * 64
        if mutation == 'expired':
            value['expired'] = True
        return value
    monkeypatch.setattr(m, 'gh', fetch)
    with pytest.raises(ValueError):
        m.recheck(original)


def test_end_recheck_preserves_unchanged_original_api(pair, monkeypatch):
    original = pair[0]
    def fetch(endpoint):
        if '/runs/' in endpoint:
            return copy.deepcopy(original['run'])
        artifact_id = int(endpoint.rsplit('/', 1)[-1])
        return copy.deepcopy(next(original[key] for key in ('whole_artifact', 'evidence_artifact')
                                   if original[key]['id'] == artifact_id))
    monkeypatch.setattr(m, 'gh', fetch)
    assert m.recheck(original) is None


def test_cli_source_change_after_verification_cannot_leave_pass_true(tmp_path, monkeypatch, pair):
    monkeypatch.setattr(m, 'collect', lambda run_id, *args: pair[0] if run_id == 11 else pair[1])
    monkeypatch.setattr(m, 'recheck', lambda data: None)
    original_verify = m.verify_readiness
    monkeypatch.setattr(m, 'verify_readiness', lambda *args: original_verify(*args, now=NOW))
    hashes = iter(['a' * 64, 'b' * 64])
    monkeypatch.setattr(m, 'digest_file', lambda path: next(hashes))
    out = tmp_path / 'acceptance.json'
    result = m.main(['--producer-run', '11', '--consumer-run', '22', '--strategy-sha', PIN,
                     '--not-after-date', '2026-11-05', '--out', str(out)])
    assert result == 1
    report = json.loads(out.read_bytes())
    assert report['pass'] is False and 'source changed' in report['error']
