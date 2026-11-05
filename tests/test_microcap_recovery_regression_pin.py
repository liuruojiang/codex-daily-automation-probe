"""Execute the workflow resolver: maintenance must test the requested runtime."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow():
    return yaml.load((ROOT / '.github/workflows/delivery-regression.yml').read_text(encoding='utf8'), Loader=yaml.BaseLoader)


@pytest.mark.parametrize('mode', ['normal', 'candidate', 'wrong_runtime', 'wrong_role', 'missing', 'invalid_sha', 'icim_candidate'])
def test_actual_resolver_preserves_normal_pin_and_checks_candidate(tmp_path, mode):
    steps = workflow()['jobs']['strategy-tests']['steps']
    step = next(s for s in steps if s.get('id') == 'pin')
    code = step['run'].split("python - <<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
    directory = tmp_path / 'automation/.github/workflows'
    directory.mkdir(parents=True)
    repo = 'liuruojiang/ic-im-rolling-arbitrage' if mode == 'icim_candidate' else 'liuruojiang/microcap'
    (directory / 'example.yml').write_text('repository: ' + repo + '\nref: ' + 'a' * 40)
    config = {'strategy_sha': 'b' * 40, 'daily_test_source': {'runtime_sha': 'b' * 40}}
    if mode == 'wrong_runtime': config['strategy_sha'] = 'c' * 40
    if mode == 'wrong_role': config['daily_test_source']['runtime_sha'] = 'c' * 40
    candidate = tmp_path / 'automation/config/microcap_recovery_candidate.json'
    candidate.parent.mkdir()
    if mode != 'missing': candidate.write_text(json.dumps(config))
    output = tmp_path / 'outputs.txt'
    env = {**os.environ, 'STRATEGY_REPOSITORY': repo, 'STRATEGY_WORKFLOW': 'example.yml',
           'GITHUB_OUTPUT': str(output), 'RECOVERY_STRATEGY_SHA': '' if mode == 'normal' else ('main' if mode == 'invalid_sha' else 'b' * 40)}
    result = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', code], cwd=tmp_path,
                            env=env, capture_output=True, timeout=10)
    if mode in ('normal', 'candidate'):
        assert result.returncode == 0, result.stderr
        values = dict(line.split('=', 1) for line in output.read_text().splitlines())
        assert values['sha'] == ('a' if mode == 'normal' else 'b') * 40
        assert values['config'] == 'automation/config/microcap_' + ('approved_state' if mode == 'normal' else 'recovery_candidate') + '.json'
    else:
        assert result.returncode != 0
        assert not output.exists()


def test_caller_and_materializer_bind_resolver_output():
    caller = yaml.load((ROOT / '.github/workflows/microcap-realtime-digest.yml').read_text(encoding='utf8'), Loader=yaml.BaseLoader)
    assert caller['jobs']['regression']['with']['recovery_strategy_sha'] == "${{ inputs.recovery_strategy_sha || '' }}"
    steps = workflow()['jobs']['strategy-tests']['steps']
    source = next(s for s in steps if s.get('name') == 'Verify and materialize the separate complete Microcap test source')
    assert '--config "${{ steps.pin.outputs.config }}"' in source['run']
    guard = next(s for s in caller['jobs']['check-trading-day']['steps'] if s.get('name') == 'Reject candidate runtime outside recovery-only maintenance')
    assert "e['RECOVERY_MODE'] not in {'bootstrap', 'require_hit'}" in guard['run']
