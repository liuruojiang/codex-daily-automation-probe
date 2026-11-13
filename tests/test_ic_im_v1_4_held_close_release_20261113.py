"""New narrow close source must be bound at producer and consumer release gates."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import verify_icim_source_release as verifier
import build_ic_im_v1_4_digest as digest
from datetime import datetime, timezone

CURRENT = 'docs/ic_im_held_put_close_20261113_v2_manifest.json'
PREVIOUS = 'docs/ic_im_expert_release_20261112_v2_manifest.json'
RELEASE = '20261113-held-put-close-v2'
COMMIT = 'a' * 40


def controlled_manifest(root):
    value = json.loads((ROOT / PREVIOUS).read_bytes())
    value['release'] = RELEASE
    value['files']['ic_im_held_put_close_v1.py'] = '0' * 64
    for field in ('files', 'support_files'):
        for name in value[field]:
            raw = ('controlled unchanged source for ' + name).encode()
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            value[field][name] = hashlib.sha256(raw).hexdigest()
    return value


def install(root, value, monkeypatch, *, committed_extra=()):
    raw = json.dumps(value).encode()
    target = root / CURRENT
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    def git(args, **kwargs):
        if args[-2:] == ['rev-parse', 'HEAD']:
            return COMMIT + '\n'
        if 'ls-tree' in args:
            return '\n'.join([CURRENT, *committed_extra]) + '\n'
        assert args[-2:] == ['show', COMMIT + ':' + CURRENT]
        return raw
    monkeypatch.setattr(verifier.subprocess, 'check_output', git)
    return target


def test_new_complete_inventory_is_selected_and_binds_close_validator(tmp_path, monkeypatch):
    value = controlled_manifest(tmp_path)
    install(tmp_path, value, monkeypatch, committed_extra=(PREVIOUS,))
    result = verifier.verify(tmp_path, COMMIT)
    assert result['release'] == RELEASE
    assert len(result['source_sha256']) == 32
    assert result['source_sha256'] == value['files']
    assert result['support_sha256'] == value['support_files']


@pytest.mark.parametrize('fault', ['missing_validator', 'validator_byte', 'source_byte',
                                  'support_byte', 'manifest_byte', 'missing_support'])
def test_new_inventory_rejects_all_changed_runtime_dependencies(tmp_path, monkeypatch, fault):
    value = controlled_manifest(tmp_path)
    if fault == 'missing_validator':
        value['files'].pop('ic_im_held_put_close_v1.py')
    if fault == 'missing_support':
        value['support_files'].pop('data/exchange_calendar/state_council_2027_v1.json')
    target = install(tmp_path, value, monkeypatch)
    if fault == 'validator_byte':
        (tmp_path / 'ic_im_held_put_close_v1.py').write_bytes(b'changed')
    elif fault == 'source_byte':
        (tmp_path / 'poe_ic_im_mainline_v1_4_bot.py').write_bytes(b'changed')
    elif fault == 'support_byte':
        (tmp_path / 'requirements-poe-v1.4-server.txt').write_bytes(b'changed')
    elif fault == 'manifest_byte':
        target.write_bytes(target.read_bytes() + b' ')
    with pytest.raises(ValueError):
        verifier.verify(tmp_path, COMMIT)


def test_same_count_cannot_hide_missing_close_validator(tmp_path, monkeypatch):
    value = controlled_manifest(tmp_path)
    value['files']['unrelated.py'] = value['files'].pop('ic_im_held_put_close_v1.py')
    (tmp_path / 'unrelated.py').write_bytes((tmp_path / 'ic_im_held_put_close_v1.py').read_bytes())
    install(tmp_path, value, monkeypatch)
    with pytest.raises(ValueError, match='held-put close'):
        verifier.verify(tmp_path, COMMIT)


def test_missing_highest_committed_manifest_cannot_downgrade(tmp_path, monkeypatch):
    value = controlled_manifest(tmp_path)
    target = install(tmp_path, value, monkeypatch, committed_extra=(PREVIOUS,))
    target.unlink()
    with pytest.raises(ValueError, match='pinned release manifest is missing'):
        verifier.verify(tmp_path, COMMIT)


def test_current_daily_and_readiness_bind_same_release_and_immutable_pin():
    daily = (ROOT / '.github/workflows/ic-im-v1-4-daily-digest.yml').read_text()
    ready = (ROOT / '.github/workflows/ic-im-v1-4-delivery-readiness.yml').read_text()
    assert f'--expected-source-release {RELEASE}' in daily
    assert f'--expected-source-release {RELEASE}' in ready
    pins = re.findall(r'repository: liuruojiang/ic-im-rolling-arbitrage\s+ref: ([0-9a-f]{40})\b', daily)
    assert len(pins) == 1
    assert daily.count('--expected-strategy-sha ' + pins[0]) == 1
    assert daily.count('--expected-source-sha ' + pins[0]) == 1
    assert '--expected-source-sha ${{ steps.pin.outputs.sha }}' in ready


def test_current_inventory_adds_only_three_required_suites_and_no_exclusion(tmp_path):
    prior = json.loads((ROOT / PREVIOUS).read_bytes())
    current = json.loads((ROOT / CURRENT).read_bytes())
    assert current['release'] == RELEASE
    assert len(current['files']) == 32
    assert current['files'].keys() == prior['files'].keys() | {'ic_im_held_put_close_v1.py'}
    assert current['required_local_tests'] == prior['required_local_tests'] + [
        'test_ic_im_held_put_close_v1.py', 'test_ic_im_held_close_evidence_v1.py',
        'test_ic_im_held_put_close_integration_v1.py']
    assert len(current['required_local_tests']) == 59
    assert current['daily_gate_deselections'] == prior['daily_gate_deselections']
    assert current['historical_exclusion_registry'] == prior['historical_exclusion_registry']
    assert current['historical_exclusion_registry_sha256'] == prior['historical_exclusion_registry_sha256']
    assert current['support_files'] == prior['support_files']
    assert current['daily_gate_outer_timeout_seconds'] == 60


def test_renderer_accepts_exact_new_inventory_proof_with_all_support(tmp_path):
    raw = (ROOT / CURRENT).read_bytes()
    current = json.loads(raw)
    proof = {'status': 'verified', 'release': RELEASE, 'strategy_sha': COMMIT,
             'manifest_sha256': hashlib.sha256(raw).hexdigest(),
             'source_sha256': current['files'], 'support_sha256': current['support_files'],
             'verified_at': datetime.now(timezone.utc).isoformat()}
    path = tmp_path / 'proof.json'
    path.write_text(json.dumps(proof), encoding='utf-8')
    assert digest.source_provenance(str(path), COMMIT, RELEASE) == proof
