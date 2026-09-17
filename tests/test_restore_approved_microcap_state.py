import hashlib
import io
import json
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import restore_approved_microcap_state as seed

URL = 'https://github.com/liuruojiang/microcap/releases/download/approved-test/state.zip'


@pytest.mark.parametrize('url', ['http://github.com/liuruojiang/microcap/releases/download/tag/state.zip', 'https://example.com/state.zip', URL + '?x=1', URL.replace('/microcap/', '/another/')])
def test_untrusted_asset_rejected(url):
    with pytest.raises(ValueError):
        seed.validate_seed(url, 'a' * 64, '2026-09-17')


def test_exact_release_identity_required():
    seed.validate_seed(URL, 'a' * 64, '2026-09-17')
    with pytest.raises(ValueError):
        seed.validate_seed(URL, 'not-a-hash', '2026-09-17')
    with pytest.raises(ValueError):
        seed.validate_seed(URL, 'a' * 64, '2026-09-99')


def test_download_checks_hash_before_returning_bytes():
    data = b'exact approved test bytes'
    with patch.object(seed.urllib.request, 'urlopen', return_value=io.BytesIO(data)):
        assert seed.download_seed(URL, hashlib.sha256(data).hexdigest()) == data
    with patch.object(seed.urllib.request, 'urlopen', return_value=io.BytesIO(data)):
        with pytest.raises(ValueError, match='SHA-256 mismatch'):
            seed.download_seed(URL, 'a' * 64)


def test_failed_whole_manifest_restore_never_reports_success(tmp_path, monkeypatch):
    data = b'approved bytes'
    output = tmp_path / 'output'
    monkeypatch.setenv('GITHUB_OUTPUT', str(output))
    monkeypatch.setattr(sys, 'argv', ['restore', '--root', str(tmp_path / 'strategy'), '--bundle', str(tmp_path / 'bundle.zip'), '--url', URL, '--sha256', hashlib.sha256(data).hexdigest(), '--seed-date', '2026-09-17'])
    monkeypatch.setattr(seed, 'download_seed', lambda *args: data)
    def reject(command, **kwargs):
        assert 'scripts' in command[1] and 'top100_cloud_delivery.py' in command[1]
        assert command[2] == 'restore'
        assert kwargs == {'check': True}
        raise seed.subprocess.CalledProcessError(1, command)
    monkeypatch.setattr(seed.subprocess, 'run', reject)
    with pytest.raises(seed.subprocess.CalledProcessError):
        seed.main()
    assert not output.exists()


def test_explicit_seed_precedes_and_excludes_old_state_restore():
    workflow = yaml.safe_load((ROOT / '.github/workflows/microcap-realtime-digest.yml').read_text(encoding='utf-8'))
    steps = workflow['jobs']['send']['steps']
    lookup = {step.get('name'): step for step in steps}
    approved = lookup['Restore explicitly approved whole state']
    assert "inputs.approved_state_url != ''" in approved['if']
    for name in ['Restore verified production state bundle', 'Restore durable verified production state bundle', 'Restore verified production state']:
        assert "inputs.approved_state_url == ''" in lookup[name]['if']
        assert steps.index(approved) < steps.index(lookup[name])
    assert "steps.approved_state.outputs.restored == 'true'" in lookup['Refresh Top100 realtime state']['if']
    on = workflow.get('on', workflow.get(True))
    assert on['workflow_dispatch']['inputs']['approved_state_url']['default'] == ''



@pytest.mark.parametrize('changed', ['', 'strategy_sha', 'state_sha256', 'schema_version'])
def test_default_release_config_is_exact_and_strategy_bound(tmp_path, changed):
    config = {'schema_version': 1, 'strategy_sha': 'a' * 40,
              'state_url': URL, 'state_sha256': 'b' * 64, 'state_date': '2026-09-17'}
    if changed:
        config[changed] = 'invalid'
    path = tmp_path / 'approved.json'
    path.write_text(json.dumps(config), encoding='utf-8')
    if changed:
        with pytest.raises(ValueError):
            seed.load_release_config(path, 'a' * 40)
    else:
        assert seed.load_release_config(path, 'a' * 40)['state_date'] == '2026-09-17'


@pytest.mark.parametrize('cache,formal,explicit,expected', [
    ('0', '', '', False), ('0', '1', '', False), ('1', '0', '', False),
    ('', '', '', True), ('1', '1', '', True), ('1', '1', URL, False),
])
def test_default_release_only_runs_when_no_qualified_state(cache, formal, explicit, expected):
    workflow = yaml.safe_load((ROOT / '.github/workflows/microcap-realtime-digest.yml').read_text(encoding='utf-8'))
    steps = workflow['jobs']['send']['steps']
    lookup = {step.get('name'): step for step in steps}
    condition = lookup['Restore approved release fallback']['if']
    values = {'steps.delivery_gate.outputs.should_send': 'true',
              'steps.full_cache.outputs.exit_code': '0',
              'inputs.approved_state_url': explicit,
              'steps.cached_state_restore.outputs.exit_code': cache,
              'steps.verified_state_restore.outputs.exit_code': formal}
    expression = re.sub(r'(?:steps|inputs)\.[a-zA-Z0-9_.-]+', lambda m: repr(values[m[0]]), condition)
    expression = expression.replace('always()', 'True').replace('&&', ' and ').replace('||', ' or ')
    assert eval(expression, {'__builtins__': {}}, {}) is expected
    assert steps.index(lookup['Validate and restore cached production state']) < steps.index(lookup['Restore durable verified production state bundle'])
    assert steps.index(lookup['Restore verified production state']) < steps.index(lookup['Restore approved release fallback'])
    assert "steps.cached_state_restore.outputs.exit_code != '0'" in lookup['Restore durable verified production state bundle']['if']
    assert '--require-success' in lookup['Restore durable verified production state bundle']['run']
    assert 'microcap-verified-state-recovery-v2-${{ steps.microcap_sha.outputs.sha }}' in lookup['Restore durable verified production state bundle']['run']

@pytest.mark.parametrize('recovered,expected', [('0', True), ('1', False), ('', False)])
def test_failed_legacy_bootstrap_requires_real_recovered_full_cache_validation(recovered, expected):
    workflow = yaml.safe_load((ROOT / '.github/workflows/microcap-realtime-digest.yml').read_text(encoding='utf-8'))
    steps = workflow['jobs']['send']['steps']
    lookup = {step.get('name'): step for step in steps}
    validate = lookup['Validate recovered full rebalance cache']
    assert 'full_rebalance_cache_bundle.py validate' in validate['run']
    assert '--min-symbols 4500' in validate['run']
    assert 'status=${PIPESTATUS[0]}' in validate['run']
    assert steps.index(lookup['Restore approved release fallback']) < steps.index(validate) < steps.index(lookup['Refresh Top100 realtime state'])
    values = {'steps.delivery_gate.outputs.should_send': 'true',
              'steps.full_cache.outputs.exit_code': '1',
              'steps.recovered_full_cache.outputs.exit_code': recovered,
              'steps.approved_state.outputs.restored': '',
              'inputs.approved_state_url': '',
              'steps.cached_state_restore.outputs.exit_code': '',
              'steps.verified_state_restore.outputs.exit_code': '',
              'steps.approved_release_fallback.outputs.restored': 'true'}
    def evaluate(condition):
        expression = re.sub(r'(?:steps|inputs)\.[a-zA-Z0-9_.-]+', lambda m: repr(values[m[0]]), condition)
        return eval(expression.replace('always()', 'True').replace('&&', ' and ').replace('||', ' or '), {'__builtins__': {}}, {})
    assert evaluate(lookup['Restore approved release fallback']['if']) is True
    assert evaluate(validate['if']) is True
    assert evaluate(lookup['Refresh Top100 realtime state']['if']) is expected
    assert evaluate(lookup['Resolve same-day static refresh mode']['if']) is expected
    assert 'steps.recovered_full_cache.outputs.exit_code' in lookup['Record refresh failure for digest']['if']
    for name in ['Restore persistent full rebalance cache', 'Bootstrap full rebalance cache on cold start', 'Restore verified production state bundle', 'Restore durable verified production state bundle']:
        assert lookup[name]['continue-on-error'] is True
        assert 'steps.full_cache.outputs.exit_code' not in lookup[name]['if']
    explicit = lookup['Restore explicitly approved whole state']['if']
    assert 'steps.full_cache' not in explicit
    assert 'always()' in explicit
