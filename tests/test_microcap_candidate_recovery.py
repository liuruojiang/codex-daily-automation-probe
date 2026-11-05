"""Run the actual workflow guard and config preparation without a cloud send."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
JOBS = yaml.safe_load((ROOT / '.github/workflows/microcap-realtime-digest.yml').read_text(encoding='utf-8'))['jobs']


def step(job, name):
    return next(s for s in JOBS[job]['steps'] if s.get('name') == name)


def run_embedded(item, cwd, env):
    source = item['run'].split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
    return subprocess.run([sys.executable, '-B', '-c', source], cwd=cwd,
                          env={**os.environ, **env}, capture_output=True, text=True)


def settings(**changes):
    return dict(CANDIDATE_SHA='a' * 40, RECOVERY_MODE='bootstrap', VALIDATION_ONLY='false',
                CORRECTION='false', EXTERNAL_SCHEDULE='false', DELIVERY_TEST_ID='',
                PUBLICATION_MODE='close_confirmed', **changes)


@pytest.mark.parametrize('field,value', [('CANDIDATE_SHA', 'main'), ('CANDIDATE_SHA', 'a;echo bad'),
    ('RECOVERY_MODE', 'off'), ('VALIDATION_ONLY', 'true'), ('CORRECTION', 'true'),
    ('EXTERNAL_SCHEDULE', 'true'), ('DELIVERY_TEST_ID', 'test-id'), ('PUBLICATION_MODE', 'realtime')])
def test_candidate_cannot_enter_send_routes(tmp_path, field, value):
    env = settings()
    env[field] = value
    result = run_embedded(step('check-trading-day', 'Reject candidate runtime outside recovery-only maintenance'), tmp_path, env)
    assert result.returncode != 0


@pytest.mark.parametrize('mode', ['bootstrap', 'require_hit'])
def test_exact_candidate_recovery_is_allowed(tmp_path, mode):
    env = settings()
    env['RECOVERY_MODE'] = mode
    assert run_embedded(step('check-trading-day', 'Reject candidate runtime outside recovery-only maintenance'), tmp_path, env).returncode == 0


def test_candidate_config_preserves_approved_seed_and_production_config(tmp_path):
    original = (ROOT / 'config/microcap_approved_state.json').read_bytes()
    path = tmp_path / 'automation/config/microcap_approved_state.json'
    path.parent.mkdir(parents=True)
    path.write_bytes(original)
    env = {'CANDIDATE_SHA': 'a' * 40, 'GITHUB_OUTPUT': str(tmp_path / 'output')}
    result = run_embedded(step('recovery-check', 'Resolve approved recovery strategy pin'), tmp_path, env)
    assert result.returncode == 0, result.stderr
    expected = json.loads(original)
    expected['strategy_sha'] = 'a' * 40
    assert json.loads((tmp_path / 'recovery-config.json').read_bytes()) == expected
    assert path.read_bytes() == original
    assert 'CANDIDATE_SHA' not in json.dumps(JOBS['send'])
    assert 'recovery_strategy_sha' not in json.dumps(JOBS['send'])
    assert step('recovery-check', 'Require the recovery candidate to be a published mainline commit')['run'] == 'git merge-base --is-ancestor HEAD origin/main'


@pytest.mark.parametrize('bad_field', [None, 'state_sha256', 'strategy_sha'])
def test_reviewed_candidate_uses_original_restore_then_exact_candidate(tmp_path, bad_field):
    original = json.loads((ROOT / 'config/microcap_approved_state.json').read_bytes())
    path = tmp_path / 'automation/config'
    path.mkdir(parents=True)
    (path / 'microcap_approved_state.json').write_text(json.dumps(original))
    candidate = {**original, 'strategy_sha': 'a' * 40, 'bootstrap_strategy_sha': 'b' * 40,
                 'source_repack_changes': {'scripts/example.py': ['c' * 64, 'd' * 64]}}
    if bad_field:
        candidate[bad_field] = 'e' * (64 if bad_field == 'state_sha256' else 40)
    (path / 'microcap_recovery_candidate.json').write_text(json.dumps(candidate))
    result = run_embedded(step('recovery-check', 'Resolve approved recovery strategy pin'), tmp_path,
                          {'CANDIDATE_SHA': 'a' * 40, 'GITHUB_OUTPUT': str(tmp_path / 'output')})
    if bad_field:
        assert result.returncode != 0
        assert not (tmp_path / 'recovery-config.json').exists()
    else:
        assert result.returncode == 0, result.stderr
        assert json.loads((tmp_path / 'recovery-config.json').read_bytes()) == candidate
        assert json.loads((tmp_path / 'recovery-bootstrap-config.json').read_bytes())['strategy_sha'] == 'b' * 40
        names = [s.get('name') for s in JOBS['recovery-check']['steps']]
        assert names.index('Select the reviewed original runtime for approved bootstrap restore') < names.index('Restore approved bootstrap only when no recovery artifact exists') < names.index('Return to the exact candidate after original approved restore') < names.index('Repack approved restored state for the exact runtime')
