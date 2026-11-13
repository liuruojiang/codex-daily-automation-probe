"""Exact local/cloud pins and recorded D11 source-selection/native-reader chain."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import member_checkpoint_policy as policy
import prepare_microcap_member_local_bundle as bundle
import restore_microcap_member_checkpoint as restore
import microcap_recovery_handoff as recovery

FIXTURE = ROOT / 'tests/fixtures/member_checkpoint_local_read_20261111'
AB = policy.LOCAL_READ_POLICY['execution_strategy_sha']
NAMESPACE = policy.LOCAL_READ_POLICY['checkpoint_strategy_sha']
CORE = policy.LOCAL_READ_POLICY['core_strategy_sha']


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def real_transport(monkeypatch, changed_core=False):
    metadata = json.loads((FIXTURE / 'metadata.json').read_bytes())
    raw = (FIXTURE / 'checkpoint-13838054871.zip').read_bytes()
    assert digest(raw) == metadata['D11_zip_sha256']
    assert digest((FIXTURE / 'ab214_member_period_handoff.py').read_bytes().replace(b'\r\n', b'\n')) == metadata['native_verifier_sha256_lf']
    if changed_core:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            bodies = {n: archive.read(n) for n in archive.namelist()}
        cp = json.loads(bodies['checkpoint.json'])
        cp['strategy_sha'] = 'f' * 40
        bodies['checkpoint.json'] = json.dumps(cp).encode()
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            for name, body in bodies.items():
                archive.writestr(name, body)
        raw = stream.getvalue()
    base = 'https://api.github.com/repos/liuruojiang/codex-daily-automation-probe/actions'
    routes = {base + '/artifacts?per_page=100&page=1': {'artifacts': metadata['artifacts']}}
    for identity in ('42588400092', '42983576468'):
        routes[base + '/runs/' + identity] = metadata['runs'][identity]
        routes[base + '/runs/' + identity + '/jobs?per_page=100'] = metadata['jobs'][identity]
    downloads = []
    def read(request, **kwargs):
        assert request.full_url in routes
        return io.BytesIO(json.dumps(routes[request.full_url]).encode())
    def download(artifact, token):
        downloads.append(artifact['id'])
        assert artifact['id'] == 13838054871, 'Exact 477 selector must not fall back to the old D9 namespace'
        return raw
    monkeypatch.setattr(restore.urllib.request, 'urlopen', read)
    monkeypatch.setattr(restore, 'download', download)
    monkeypatch.setenv('GITHUB_TOKEN', 'offline-transport-fixture')
    return raw, downloads


def invoke(monkeypatch, out, extra=()):
    monkeypatch.setattr(sys, 'argv', ['restore', '--strategy-sha', AB,
        '--checkpoint-read-policy', str(ROOT / 'config/microcap_member_local_runtime.json'),
        '--repository', 'liuruojiang/codex-daily-automation-probe', '--not-after-date', '2026-11-11',
        '--seed-config', str(ROOT / 'config/microcap_member_checkpoint.json'), '--out', str(out), *extra])
    restore.main()


def test_exact_policy_discovers_original_D11_and_native_ab214_verifies_without_history_rewrite(
        tmp_path, monkeypatch, capsys, record_property):
    raw, downloads = real_transport(monkeypatch)
    out = tmp_path / 'restored'
    invoke(monkeypatch, out)
    report = json.loads(capsys.readouterr().out.strip())
    assert report['artifact_id'] == 13838054871 and downloads == [13838054871]
    assert report['strategy_sha'] == AB and report['checkpoint_strategy_sha'] == NAMESPACE
    assert report['checkpoint_read_policy'] == policy.LOCAL_READ_POLICY['policy']
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        assert all((out / name).read_bytes() == archive.read(name) for name in archive.namelist())
    cp = json.loads((out / 'checkpoint.json').read_bytes())
    assert cp['strategy_sha'] == CORE and cp['as_of_date'] == '2026-11-11'
    prior_raw = (ROOT / 'config/microcap_member_seed_20261109_4774966d.zip').read_bytes()
    with zipfile.ZipFile(io.BytesIO(prior_raw)) as archive:
        history_prefix = archive.read('history.csv')
    assert (out / 'history.csv').read_bytes().startswith(history_prefix)
    verifier = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(FIXTURE / 'ab214_member_period_handoff.py'),
        'verify', '--checkpoint', str(out / 'checkpoint.json')], capture_output=True, text=True, check=True)
    verified = json.loads(verifier.stdout)
    assert verified['as_of_date'] == '2026-11-11' and verified['next_trade_date'] == '2026-11-12'
    record_property('real_D11_contract_acceptance', json.dumps(dict(
        artifact_id=report['artifact_id'], execution_sha=AB, checkpoint_namespace=NAMESPACE, core_sha=CORE,
        original_zip_sha256=digest(raw), all_payload_bytes_unchanged=True,
        native_ab214_verify_exit_code=verifier.returncode,
        original_D9_history_prefix_sha256=digest(history_prefix), original_history_prefix_unchanged=True,
        model_parameters_changed=False, SMTP=False, production_installation_changed=False)))


def test_global_cloud477_restore_still_uses_exact_477_namespace(tmp_path, monkeypatch, capsys):
    raw, downloads = real_transport(monkeypatch)
    out = tmp_path / 'cloud'
    monkeypatch.setattr(sys, 'argv', ['restore', '--strategy-sha', NAMESPACE,
        '--repository', 'liuruojiang/codex-daily-automation-probe', '--out', str(out), '--not-after-date', '2026-11-11'])
    restore.main()
    result = json.loads(capsys.readouterr().out.strip())
    assert result['artifact_id'] == 13838054871 and downloads == [13838054871]
    assert result['strategy_sha'] == result['checkpoint_strategy_sha'] == NAMESPACE
    assert result['checkpoint_read_policy'] is None
    assert policy.local_runtime(ROOT) == (AB, ROOT / 'config/microcap_member_local_runtime.json')


@pytest.mark.parametrize('field,value', [
    ('execution_strategy_sha', 'a' * 40), ('checkpoint_strategy_sha', 'b' * 40),
    ('core_strategy_sha', 'c' * 40), ('policy', 'arbitrary_version_alias'), ('extra', 'unreviewed')])
def test_unknown_relationship_is_rejected_before_network_or_output(tmp_path, monkeypatch, field, value):
    altered = copy.deepcopy(policy.LOCAL_READ_POLICY)
    altered[field] = value
    path = tmp_path / 'policy.json'
    path.write_text(json.dumps(altered))
    monkeypatch.setattr(restore, 'fetch_latest', lambda *a, **k: pytest.fail('Unreviewed alias cannot query GitHub'))
    monkeypatch.setattr(sys, 'argv', ['restore', '--strategy-sha', AB,
        '--checkpoint-read-policy', str(path), '--out', str(tmp_path / 'not-created')])
    with pytest.raises(ValueError, match='Unreviewed'):
        restore.main()
    assert not (tmp_path / 'not-created').exists()


def test_known_policy_rejects_different_execution_pin():
    with pytest.raises(ValueError, match='Unreviewed'):
        policy.load_read_policy(ROOT / 'config/microcap_member_local_runtime.json', NAMESPACE)


def test_core_mismatch_cannot_be_hidden_by_known_artifact_namespace(tmp_path, monkeypatch):
    real_transport(monkeypatch, changed_core=True)
    out = tmp_path / 'not-installed'
    with pytest.raises(ValueError, match='core identity'):
        invoke(monkeypatch, out)
    assert not out.exists()


def test_explicit_cached_checkpoint_cannot_bypass_the_same_core_identity_guard():
    known = policy.load_read_policy(ROOT / 'config/microcap_member_local_runtime.json', AB)
    policy.validate_checkpoint_payload({'strategy_sha': CORE}, known)
    with pytest.raises(ValueError, match='core identity'):
        policy.validate_checkpoint_payload({'strategy_sha': 'd' * 40}, known)


@pytest.mark.parametrize('producer', policy.LOCAL_READ_POLICY['rebalance_producer_shas'])
def test_exact_reviewed_rebalance_marker_requires_preserved_native_handoff(producer):
    known = policy.load_read_policy(ROOT / 'config/microcap_member_local_runtime.json', AB)
    error='formal recovery proof route' if producer in (recovery.M8,recovery.M9,recovery.M10) else 'handoff lineage'
    with pytest.raises(ValueError, match=error):
        policy.validate_checkpoint_payload({'strategy_sha': producer}, known)
    if producer in (recovery.M8,recovery.M9,recovery.M10):
        # This checks the policy header, not the independent complete-proof gate.
        value=dict(strategy_sha=producer,handoff_mode=recovery.MODE,handoff_from='1'*64,
            recovery_controller=dict(revision=recovery.CONTROLLER,
                source_sha256=digest(Path(recovery.__file__).read_bytes().replace(b'\r\n',b'\n'))))
        before=copy.deepcopy(value)
        policy.validate_checkpoint_payload(value,known)
        assert value==before
        for fault,error in (('handoff_mode','formal recovery proof route'),
                            ('handoff_from','handoff lineage'),('recovery_controller','Unreviewed recovery controller')):
            altered=copy.deepcopy(value)
            if fault=='recovery_controller':altered.pop(fault)
            else:altered[fault]='unreviewed'
            with pytest.raises(ValueError,match=error):policy.validate_checkpoint_payload(altered,known)
        return
    value = dict(strategy_sha=producer, handoff_mode='native_close_extends_previous', handoff_from='1'*64)
    for mode in ('native_close_extends_previous', 'same_close_period_switch'):
        policy.validate_checkpoint_payload(dict(value, handoff_mode=mode), known)
    for fault in ('handoff_mode', 'handoff_from'):
        altered = dict(value, **{fault: 'unreviewed'})
        with pytest.raises(ValueError, match='handoff lineage'):
            policy.validate_checkpoint_payload(altered, known)


def test_reader_policy_cannot_relabel_a_checkpoint_for_publication(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['describe', '--strategy-sha', AB,
        '--checkpoint-read-policy', str(ROOT / 'config/microcap_member_local_runtime.json'),
        '--describe-checkpoint', str(tmp_path / 'not-needed.json')])
    with pytest.raises(SystemExit):
        restore.main()


@pytest.mark.parametrize('add_report_contract', [False, True])
def test_bundle_materializes_exact_git_objects_without_activating(tmp_path, monkeypatch, add_report_contract):
    source = tmp_path / 'source'
    source.mkdir()
    names = [*bundle.BUNDLE_FILES, *[p for p in bundle.LOCAL_DEPENDENCIES.values() if (ROOT/p).exists()]]
    for name in names:
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    if add_report_contract:
        contract = bundle.LOCAL_DEPENDENCIES['microcap_member_report_contract']
        if contract not in names:
            names.append(contract)
            (source/contract).write_text('# software-only dependency closure fixture\n')
        builder = source/'scripts/build_microcap_member_digest.py'
        builder.write_bytes(builder.read_bytes()+b'\nimport microcap_member_report_contract\n')
    subprocess.run(['git', 'init', '-q', str(source)], check=True)
    subprocess.run(['git', '-C', str(source), 'add', '--all'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(source), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '-qm', 'Frozen software test files'], check=True, capture_output=True)
    revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    strategy = tmp_path / 'strategy'
    strategy.mkdir()
    original = bundle.subprocess.check_output
    def read(command, **kwargs):
        if command == ['git', '-C', str(strategy.resolve()), 'rev-parse', 'HEAD']:
            return AB + '\n'
        return original(command, **kwargs)
    monkeypatch.setattr(bundle.subprocess, 'check_output', read)
    out = tmp_path / 'candidate'
    result = bundle.prepare(source, revision, strategy, out)
    assert result['status'] == 'local_member_bundle_prepared_not_installed'
    assert result['production_installation_changed'] is False
    manifest = json.loads((out / 'manifest.json').read_bytes())
    assert set(manifest['files']) == set(names)
    for name in names:
        expected = original(['git', '-C', str(source), 'show', revision + ':' + name])
        assert (out / name).read_bytes() == expected
    candidate = json.loads(Path(result['candidate_pointer']).read_bytes())
    assert candidate['strategy_pin'] == AB and candidate['manifest_sha256'] == digest((out / 'manifest.json').read_bytes())
    assert policy.local_runtime(out) == (AB, out / 'config/microcap_member_local_runtime.json')
    with pytest.raises(ValueError, match='already exists'):
        bundle.prepare(source, revision, strategy, out)
