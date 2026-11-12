"""Real local runner/adoption boundary against original recovery packages.

The suffix is explicitly software R12/D13. The M7 calculations/package and
installed AB214 verifier are real; no market source, task, pointer or mail runs.
"""
from __future__ import annotations

import copy
from datetime import date
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType

import pytest

from test_microcap_crossday_recovery_adversarial import (
    ROOT, SoftwareRecoveryClock, independently_built_recovery,
)

sys.path.insert(0, str(ROOT / 'scripts'))
import run_microcap_member_local as current_runner
import member_checkpoint_policy as policy


@pytest.fixture
def local_reader():
    selected = os.environ.get('MICROCAP_LOCAL_READER_ROOT')
    installed = Path(r'D:\Codex\home\automations\ic-im\member_daily_install.json')
    if selected:
        root = Path(selected).resolve()
    elif installed.is_file():
        pointer = json.loads(installed.read_bytes())
        assert pointer['strategy_pin'] == policy.LOCAL_READ_POLICY['execution_strategy_sha']
        root = Path(pointer['strategy_root']).resolve()
    else:
        pytest.fail('Requires exact AB214 local reader checkout or MICROCAP_LOCAL_READER_ROOT')
    actual = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    assert actual == policy.LOCAL_READ_POLICY['execution_strategy_sha']
    return root


def clone_package(case, out, fault=None):
    out.mkdir()
    document = copy.deepcopy(case['checkpoint'])
    payload = dict(case['payload'])
    period = json.loads(payload['period'])
    if fault == 'missing_proof':
        period.pop(case['api'].KEY)
    elif fault == 'mode_downgrade':
        document['handoff_mode'] = 'native_close_extends_previous'
    elif fault == 'rewrite_claim':
        period[case['api'].KEY]['historical_rewrite'] = True
    elif fault == 'controller_source':
        document['recovery_controller']['source_sha256'] = '1' * 64
        period[case['api'].KEY]['controller_identity']['source_sha256'] = '1' * 64
    payload['period'] = case['api'].encode(period)
    document['period']['sha256'] = hashlib.sha256(payload['period']).hexdigest()
    for role, raw in payload.items():
        (out / document[role]['path']).write_bytes(raw)
    path = out / 'checkpoint.json'
    path.write_bytes(case['api'].encode(document))
    return path


def invoke(runner, checkpoint, out, reader, monkeypatch):
    calendar = ModuleType('scripts.exchange_calendar')
    calendar.latest_completed_session = lambda: date(2026, 11, 13)
    monkeypatch.setitem(sys.modules, 'scripts.exchange_calendar', calendar)
    monkeypatch.setattr(runner, 'datetime', SoftwareRecoveryClock)
    monkeypatch.setattr(runner, 'github_environment', lambda: pytest.fail('Cached package must not fetch GitHub'))
    monkeypatch.setattr(sys, 'argv', ['run_microcap_member_local.py', '--automation-root', str(ROOT),
        '--root', str(reader), '--date', '2026-11-13', '--out', str(out),
        '--checkpoint', str(checkpoint), '--prepare-only'])
    calls = []
    original = subprocess.run
    def process(argv, **kwargs):
        if len(argv) > 2 and Path(argv[2]) == reader / 'scripts/member_period_handoff.py':
            calls.append(list(argv))
        return original(argv, **kwargs)
    monkeypatch.setattr(runner.subprocess, 'run', process)
    code = runner.main()
    return code, calls


def test_original_local_runner_accepts_invalid_recovery_cache_through_real_ab214_verify(
        independently_built_recovery, tmp_path, monkeypatch, local_reader, record_property):
    """Retain the actual pre-fix counterexample without editing working source."""
    baseline = subprocess.run(['git', '-C', str(ROOT), 'show',
        'eecf2c8fa7afb69426e9d8293517beb22f5a8c98:scripts/run_microcap_member_local.py'], capture_output=True)
    assert baseline.returncode == 0, 'Fetch original eec Git blob for the retained pre-fix counterexample'
    raw = baseline.stdout
    assert b'validate_checkpoint_directory' not in raw
    original = ModuleType('local_runner_before_recovery_cache_gate')
    original.__file__ = str(ROOT / 'scripts/run_microcap_member_local.py')
    exec(compile(raw, original.__file__, 'exec'), original.__dict__)
    checkpoint = clone_package(independently_built_recovery, tmp_path / 'original-bad', 'missing_proof')
    with pytest.raises(ValueError, match='Recovery checkpoint lacks its proof'):
        policy.validate_checkpoint_directory(checkpoint, policy.LOCAL_READ_POLICY)
    code, calls = invoke(original, checkpoint, tmp_path / 'original-run', local_reader, monkeypatch)
    assert code == 0 and len(calls) == 1
    commands = json.loads((tmp_path / 'original-run/commands.json').read_bytes())
    assert commands[0]['exit_code'] == 0
    record_property('actual_old_runner_counterexample', json.dumps(dict(
        baseline='eecf2c8fa7afb69426e9d8293517beb22f5a8c98',
        source_sha256=hashlib.sha256(raw).hexdigest(), deleted_recovery_proof=True,
        original_actual_ab214_verify_exit=0, old_runner_prepared=True,
        software_suffix=True, SMTP=False, production_pointer_changed=False)))


def test_current_local_runner_accepts_complete_recovery_cache_with_real_ab214_verify(
        independently_built_recovery, tmp_path, monkeypatch, local_reader, capsys, record_property):
    checkpoint = clone_package(independently_built_recovery, tmp_path / 'complete')
    before = {p.name: p.read_bytes() for p in checkpoint.parent.iterdir()}
    code, calls = invoke(current_runner, checkpoint, tmp_path / 'current-run', local_reader, monkeypatch)
    assert code == 0 and len(calls) == 1
    assert json.loads(capsys.readouterr().out.strip())['status'] == 'prepared'
    assert {p.name: p.read_bytes() for p in checkpoint.parent.iterdir()} == before
    record_property('recovery_cached_source_unchanged', True)


@pytest.mark.parametrize('fault', ['missing_proof', 'mode_downgrade', 'rewrite_claim', 'controller_source'])
def test_current_local_runner_rejects_coherently_rehashed_recovery_cache_before_native_verify(
        independently_built_recovery, tmp_path, monkeypatch, local_reader, fault):
    checkpoint = clone_package(independently_built_recovery, tmp_path / fault, fault)
    before = {p.name: p.read_bytes() for p in checkpoint.parent.iterdir()}
    with pytest.raises(ValueError, match='Recovery|recovery'):
        invoke(current_runner, checkpoint, tmp_path / 'rejected', local_reader, monkeypatch)
    assert not (tmp_path / 'rejected/commands.json').exists()
    assert {p.name: p.read_bytes() for p in checkpoint.parent.iterdir()} == before


@pytest.mark.parametrize('fault', ['relative_escape', 'absolute', 'backslash', 'link'])
def test_current_local_runner_rejects_cached_dependency_path_escape_before_native_verify(
        independently_built_recovery, tmp_path, monkeypatch, local_reader, fault):
    checkpoint = clone_package(independently_built_recovery, tmp_path / fault)
    document = json.loads(checkpoint.read_bytes())
    if fault == 'relative_escape':
        document['history']['path'] = '../outside-history.csv'
    elif fault == 'absolute':
        document['history']['path'] = str((tmp_path / 'outside-history.csv').resolve())
    elif fault == 'backslash':
        document['history']['path'] = '..\\outside-history.csv'
    else:
        source = checkpoint.parent / document['history']['path']
        outside = tmp_path / 'outside-history.csv'
        outside.write_bytes(source.read_bytes())
        source.unlink()
        try:
            source.symlink_to(outside)
        except OSError:
            pytest.skip('Native link creation unavailable on this test host')
    checkpoint.write_bytes(independently_built_recovery['api'].encode(document))
    with pytest.raises(ValueError, match='path escapes|contains a link'):
        invoke(current_runner, checkpoint, tmp_path / 'rejected', local_reader, monkeypatch)
    assert not (tmp_path / 'rejected/commands.json').exists()
