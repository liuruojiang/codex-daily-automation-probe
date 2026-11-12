"""Independent publication counterexamples; no market data or network writes.

The small archives below are labelled software envelopes. They test recovery
control and provenance, not native economics or production acceptance.
"""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import re
import sys
from urllib.parse import parse_qs, urlsplit
import urllib.request
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import restore_microcap_rebalance_recovery as recovery
import microcap_scoped_whole_recovery as whole
from prepare_microcap_local import encode

NATIVE, SOURCE, AUTO, DAILY = '1' * 40, '2' * 40, '3' * 40, '4' * 40
OLD, NOW = '2026-11-13', '2026-11-16'


def zip_bytes(files):
    body = io.BytesIO()
    with zipfile.ZipFile(body, 'w') as target:
        for name, raw in files.items():
            target.writestr(name, raw)
    return body.getvalue()


def envelope():
    return encode(dict(ok=True, status='complete', scope='whole_workspace_delivery',
        release_sha=NATIVE, expected_date=OLD, inputs={'software-input': 'a' * 64},
        artifacts={'software-output': 'b' * 64}, streams={'software-only': {'rows': 1}}))


@pytest.fixture
def payloads(tmp_path):
    bundle = tmp_path / 'software-whole.zip'
    bundle.write_bytes(zip_bytes({'outputs/top100_delivery_manifest.json': envelope()}))
    folder = tmp_path / 'software-checkpoint'; folder.mkdir()
    period = encode(dict(anchor_day='2026-11-12'))
    (folder / 'period.json').write_bytes(period)
    (folder / 'checkpoint.json').write_bytes(encode(dict(as_of_date=OLD, period_status='current',
        period=dict(path='period.json', sha256=hashlib.sha256(period).hexdigest()))))
    return bundle, folder / 'checkpoint.json'


class ReadOnlyCloud:
    """An independent finite GET fixture, including original run-attempt APIs."""
    def __init__(self):
        self.artifacts = []; self.runs = {}; self.jobs = {}; self.bodies = {}; self.reads = []

    def run(self, number, attempt=1):
        value = dict(id=number, status='completed', conclusion='failure', head_branch='main',
            event='workflow_dispatch', path=recovery.WORKFLOW, head_sha=AUTO, run_attempt=attempt,
            created_at=OLD + 'T07:01:00Z', repository={'full_name': recovery.REPOSITORY},
            head_repository={'full_name': recovery.REPOSITORY})
        self.runs[number] = value
        return value

    def steps(self, number, values, attempt=1):
        self.jobs[number, attempt] = [dict(run_id=number, status='completed', conclusion='failure',
            steps=deepcopy(values))]

    def store(self, number, run_id, name, files, created):
        body = zip_bytes(files)
        self.bodies[number] = body
        value = dict(id=number, name=name, expired=False, digest='sha256:' + hashlib.sha256(body).hexdigest(),
            size_in_bytes=len(body), created_at=created,
            archive_download_url=f'https://api.github.com/repos/{recovery.REPOSITORY}/actions/artifacts/{number}/zip',
            workflow_run=dict(id=run_id, head_sha=AUTO))
        self.artifacts.append(value)
        return value

    def api(self, url, token):
        self.reads.append(url)
        parsed = urlsplit(url); query = parse_qs(parsed.query); path = parsed.path
        match = re.search(r'/runs/(\d+)/attempts/(\d+)/jobs$', path)
        if match:
            return dict(jobs=self.jobs.get(tuple(map(int, match.groups())), []))
        match = re.search(r'/runs/(\d+)/jobs$', path)
        if match:
            number = int(match[1])
            return dict(jobs=self.jobs.get((number, self.runs[number].get('run_attempt', 1)), []))
        match = re.search(r'/runs/(\d+)/artifacts$', path)
        if match:
            return dict(artifacts=[a for a in self.artifacts if a['workflow_run']['id'] == int(match[1])])
        match = re.search(r'/runs/(\d+)$', path)
        if match:
            return self.runs[int(match[1])]
        if path.endswith('/artifacts'):
            return dict(artifacts=[a for a in self.artifacts if a['name'] == query.get('name', [''])[0]])
        raise AssertionError('Unexpected non-GET fixture path ' + path)

    def download(self, request, destination, **expected):
        number = int(re.search(r'/artifacts/(\d+)/zip$', request.full_url)[1])
        body = self.bodies[number]
        assert expected['expected_sha'] == hashlib.sha256(body).hexdigest()
        assert expected['expected_bytes'] == len(body)
        Path(destination).write_bytes(body)
        return dict(sha256=hashlib.sha256(body).hexdigest(), bytes=len(body))


@pytest.fixture
def cloud(monkeypatch):
    value = ReadOnlyCloud()
    monkeypatch.setattr(recovery, 'api_json', value.api)
    monkeypatch.setattr(whole, 'api_json', value.api)
    monkeypatch.setattr(recovery, 'stream_download', value.download)
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *a, **k: pytest.fail('Live HTTP forbidden'))
    return value


def partial(tmp_path, cloud, bundle, *, number, phase, formal):
    root = tmp_path / ('partial-' + str(number)); root.mkdir()
    plan = dict(schema='microcap_scoped_recovery_plan_v1', consumer_sha=NATIVE,
        delivery_date=OLD, required_rebalance_dates=['2026-11-12'])
    (root / 'scoped-recovery-plan.json').write_bytes(encode(plan))
    if formal:
        (root / 'scoped-delivery').mkdir()
        (root / 'scoped-delivery/formal-state.zip').write_bytes(bundle.read_bytes())
    manifest = root / recovery.MANIFESTS[phase]
    recovery.seal(root=root, runtime_sha=NATIVE, producer_sha=SOURCE, plan=root / 'scoped-recovery-plan.json',
        out=manifest, phase=phase, run_id=number, run_attempt=1, producer_commit=AUTO)
    cloud.run(number)
    cloud.steps(number, [dict(name=recovery.UPLOAD_STEPS[phase], status='completed', conclusion='success')])
    files = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    cloud.store(100 + number, number, recovery.PREFIXES[phase] + NATIVE, files,
        OLD + ('T07:05:00Z' if formal else 'T08:05:00Z'))
    return manifest


@pytest.mark.parametrize('new_phase', ['boundaries', 'state'])
def test_plan_false_does_not_lose_older_earned_archive_behind_new_partial(tmp_path, payloads, cloud, new_phase):
    bundle, _ = payloads
    partial(tmp_path, cloud, bundle, number=10, phase='state', formal=True)
    partial(tmp_path, cloud, bundle, number=11, phase=new_phase, formal=False)
    result = recovery.recover(runtime_sha=NATIVE, producer_sha=SOURCE, not_after=NOW,
        out=tmp_path / 'restored-orphan', token='software-fixture', orphan_only=True)
    assert result['restored'] is True and result['formal_archive']
    assert result['source_run_id'] == 10 and result['original_target'] == OLD
    assert Path(result['formal_archive']).read_bytes() == bundle.read_bytes()


def publication(tmp_path, payloads, *, run_id=20, run_attempt=1, kind='whole', **extra):
    bundle, checkpoint = payloads
    return recovery.publication(kind=kind, intent=tmp_path / (kind + '-publication-intent.json'),
        token='software-fixture', run_id=run_id, run_attempt=run_attempt, producer_commit=AUTO,
        bundle=bundle, checkpoint=checkpoint, namespace_sha=DAILY, runtime_sha=NATIVE, **extra)


@pytest.mark.parametrize('kind', ['whole', 'checkpoint'])
def test_old_failed_write_without_start_clock_is_unknown_and_cannot_repeat(tmp_path, payloads, cloud, kind):
    cloud.run(10); cloud.run(20)
    first = publication(tmp_path / 'original', payloads, kind=kind, run_id=10)
    old = Path(first['intent']); original = old.read_bytes()
    step = 'Publish verified scoped whole state' if kind == 'whole' else 'Publish verified member checkpoint'
    cloud.steps(10, [dict(name=step, status='completed', conclusion='failure', started_at=None)])
    with pytest.raises(recovery.RecoveryError):
        publication(tmp_path / 'restart', payloads, kind=kind, prior_intent=old)
    assert old.read_bytes() == original
    assert not (tmp_path / 'restart' / (kind + '-publication-intent.json')).exists()


@pytest.mark.parametrize('kind', ['whole', 'checkpoint'])
def test_rerun_reads_original_attempt_instead_of_borrowing_latest_skipped_step(tmp_path, payloads, cloud, kind):
    cloud.run(10); cloud.run(20)
    first = publication(tmp_path / 'original', payloads, run_id=10, kind=kind)
    old = Path(first['intent']); original = old.read_bytes()
    step = 'Publish verified scoped whole state' if kind == 'whole' else 'Publish verified member checkpoint'
    cloud.runs[10]['run_attempt'] = 2
    cloud.steps(10, [dict(name=step, status='completed', conclusion='failure', started_at=OLD + 'T07:06:00Z')], attempt=1)
    cloud.steps(10, [dict(name=step, status='completed', conclusion='skipped', started_at=None)], attempt=2)
    with pytest.raises(recovery.RecoveryError):
        publication(tmp_path / 'restart', payloads, kind=kind, prior_intent=old)
    assert old.read_bytes() == original
    assert any('/runs/10/attempts/1/jobs' in value for value in cloud.reads)
    assert not (tmp_path / 'restart' / (kind + '-publication-intent.json')).exists()


def test_orphan_validates_original_date_and_keeps_current_signal_false(tmp_path, payloads, cloud, monkeypatch):
    bundle, _ = payloads
    manifest = partial(tmp_path, cloud, bundle, number=10, phase='state', formal=True)
    consumer = tmp_path / 'software-native-root'; consumer.mkdir()
    stages = []
    def software_native(label, code, args, work):
        stages.append((label, list(args)))
        if label == 'whole-restore':
            # The unchanged RESTORE interface receives the archive's real old D,
            # while not_after is only an upper bound, never a relabelled signal.
            assert args[2] == OLD
            (consumer / 'outputs').mkdir(exist_ok=True)
            (consumer / whole.MANIFEST).write_bytes(envelope())
            Path(args[3]).write_bytes(encode(dict(ok=True, software_fixture=True)))
    monkeypatch.setattr(whole, 'native_stage', software_native)
    original = bundle.read_bytes()
    result = recovery.orphan_publication(consumer=consumer, manifest_path=manifest, runtime_sha=NATIVE,
        producer_sha=SOURCE, not_after=NOW, out=tmp_path / 'orphan-publish', token='software-fixture',
        run_id=20, run_attempt=1, producer_commit=AUTO)
    assert result['should_publish'] is True and result['original_target'] == OLD
    assert result['native_archive_validation']['seed_date'] == OLD
    assert result['current_signal_ready'] is False and result['formal_refresh_repeated'] is False
    assert bundle.read_bytes() == original and len(stages) == 3


@pytest.mark.parametrize('kind', ['whole', 'checkpoint'])
def test_closed_server_bytes_resolve_lost_upload_without_relabeling_failed_step(tmp_path, payloads, cloud, kind):
    bundle, checkpoint = payloads
    cloud.run(10)
    name, _, files, _, _ = recovery._publication_payload(kind, checkpoint=checkpoint, bundle=bundle,
        namespace_sha=DAILY, runtime_sha=NATIVE)
    contents = ({whole.BUNDLE_NAME: bundle.read_bytes()} if kind == 'whole'
        else {path: (checkpoint.parent / path).read_bytes() for path in files})
    cloud.store(410, 10, name, contents, OLD + 'T07:07:00Z')
    required = ['Earn original formal rebalance and next-session handoff',
        'Pack verified scoped whole state' if kind == 'whole' else 'Describe verified scoped member checkpoint']
    publish = 'Publish verified scoped whole state' if kind == 'whole' else 'Publish verified member checkpoint'
    cloud.steps(10, [dict(name=step, status='completed', conclusion='success') for step in required]
        + [dict(name=publish, status='completed', conclusion='failure', started_at=OLD + 'T07:06:00Z')])
    result = publication(tmp_path / 'reader', payloads, kind=kind)
    assert result['should_publish'] is False and result['artifact_id'] == 410
    assert result['acceptance'] == ('closed_server_object_reconciled_after_publication_response_loss'
        if kind == 'whole' else 'server_object_reconciled_after_upload_response_loss')
    assert not (tmp_path / 'reader' / (kind + '-publication-intent.json')).exists()


def test_original_orphan_cannot_shadow_a_newer_already_published_whole_archive(tmp_path, payloads, cloud):
    cloud.run(10)
    newer = json.loads(envelope()); newer['expected_date'] = NOW
    newer_zip = zip_bytes({'outputs/top100_delivery_manifest.json': encode(newer)})
    cloud.store(420, 10, whole.artifact_name(NATIVE), {whole.BUNDLE_NAME: newer_zip}, NOW + 'T07:07:00Z')
    cloud.steps(10, [dict(name=step, status='completed', conclusion='success') for step in
        ['Earn original formal rebalance and next-session handoff', 'Pack verified scoped whole state']]
        + [dict(name='Publish verified scoped whole state', status='completed', conclusion='failure',
                started_at=NOW + 'T07:06:00Z')])
    result = publication(tmp_path / 'old-orphan-reader', payloads)
    assert result['should_publish'] is False and result['newer_target'] == NOW
    assert result['target'] == OLD and result['status'] == 'publication_superseded_by_newer_whole_archive'
    assert not (tmp_path / 'old-orphan-reader/whole-publication-intent.json').exists()


@pytest.mark.parametrize('publish', [True, False])
def test_plan_false_orphan_workflow_preserves_upload_gate_and_skips_new_signal_work(publish):
    import yaml
    path = Path(__file__).resolve().parents[1] / '.github/workflows/microcap-member-maintenance.yml'
    flow = yaml.load(path.read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
    steps = flow['jobs']['scoped']['steps']
    outputs = dict(schedule=dict(run='true'), plan=dict(run='false'),
        resume=dict(restored='true', formal_archive='/software/old-D.zip'),
        orphan=dict(should_publish=str(publish).lower()), whole_publication={}, checkpoint_publication={},
        config=dict(run='true'))
    def enabled(step):
        expression = step.get('if', 'true')
        expression = re.sub(r'steps\.([\w_]+)\.outputs\.([\w_]+)',
            lambda match: repr(outputs.get(match[1], {}).get(match[2], '')), expression)
        expression = expression.replace('inputs.task', repr('rebalance_recovery'))
        expression = expression.replace('success()', 'True').replace('always()', 'True')
        expression = expression.replace('failure()', 'False').replace('cancelled()', 'False')
        expression = expression.replace('&&', ' and ').replace('||', ' or ')
        if expression == 'true': return True
        return bool(eval(expression, {'__builtins__': {}}, {}))
    active = {step.get('name') for step in steps if enabled(step)}
    assert 'Restore sealed partial scoped recovery evidence' in active
    assert 'Validate preserved earned whole archive without refreshing' in active
    for expensive in ['Prepare current close sources for exactly 300 candidates',
            'Earn original formal rebalance and next-session handoff', 'Publish verified member checkpoint',
            'Restore verified scoped whole delivery']:
        assert expensive not in active
    for gated in ['Pack verified scoped whole state', 'Preserve whole publication intent before upload',
            'Publish verified scoped whole state']:
        assert (gated in active) is publish
    names = [step.get('name') for step in steps]
    assert names.index('Preserve whole publication intent before upload') < names.index('Publish verified scoped whole state')
    journal = next(step for step in steps if step.get('name') == 'Preserve whole publication intent before upload')
    assert 'steps.orphan.outputs.intent_file' in journal['with']['path']
    assert 'steps.orphan.outputs.intent_artifact_name' in journal['with']['name']
