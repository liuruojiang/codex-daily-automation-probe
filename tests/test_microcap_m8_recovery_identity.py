"""M7 legacy-controller and M8 recovery identity boundaries, never live gates.

The old M7 proof is earned by the exact old controller and real M7 calculation.
M8 identity envelopes below are explicit software transports of those calculated
states; actual M8 full-market/formal acceptance is performed separately.
"""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from test_microcap_recovery_handoff import recovery_case, software_clock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import microcap_recovery_handoff as m
import member_checkpoint_policy as policy

LEGACY_COMMIT = 'b8d489a7da1f09db5e48ebff387b7042f9508906'
LEGACY_SHA = 'eea88c4886baaba42256a4b57050432a0e3a4e4dfe12e827f99086d3b0389b16'
M8 = '95d896fe58cc7cb84c0fcb7bd76534f62c9430f4'


def seal(cp, payload, period):
    payload['period'] = m.encode(period)
    for role in m.ROLES:
        cp[role]['sha256'] = m.sha(payload[role])
    return cp, payload


def as_runtime(source, runtime):
    """Explicit software identity envelope, rebinding every dependent byte hash."""
    cp, payload = copy.deepcopy(source[0]), copy.deepcopy(source[1])
    period = json.loads(payload['period']);proof = period[m.KEY]
    formal = proof['formal']
    manifest = json.loads(m._unblob(formal['manifest']))
    manifest['release_sha'] = runtime
    manifest_raw = m.encode(manifest)
    formal['manifest'] = m._blob(manifest_raw)
    candidate = json.loads(m._unblob(formal['checkpoint']))
    candidate_period = json.loads(m._unblob(formal['period']))
    candidate_period['source_manifest_sha256'] = m.sha(manifest_raw)
    formal['period'] = m._blob(m.encode(candidate_period))
    candidate['period']['sha256'] = formal['period']['sha256']
    candidate['strategy_sha'] = runtime
    formal['checkpoint'] = m._blob(m.encode(candidate))
    cp['strategy_sha'] = runtime
    period['source_manifest_sha256'] = m.sha(manifest_raw)
    return seal(cp, payload, period)


def package(tmp_path, cp, payload):
    for role in m.ROLES:
        (tmp_path / cp[role]['path']).write_bytes(payload[role])
    target = tmp_path / 'checkpoint.json';target.write_bytes(m.encode(cp))
    return target


@pytest.fixture(scope='module')
def original_old_m7(recovery_case, tmp_path_factory):
    # CI fetches this exact historical Git object outside pytest. Never network
    # fetch in this test and never replace the old source with a current module.
    raw = subprocess.check_output(['git', '-C', str(ROOT), 'show',
        LEGACY_COMMIT + ':scripts/microcap_recovery_handoff.py'])
    assert m.sha(raw.replace(b'\r\n', b'\n')) == LEGACY_SHA
    scratch = tmp_path_factory.mktemp('actual_old_controller')
    source = scratch / 'legacy_recovery_handoff.py';source.write_bytes(raw)
    spec = importlib.util.spec_from_file_location('actual_old_m7_recovery', source)
    old = importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
    assert old.NATIVE == m.NATIVE and old.NATIVE != M8
    # The shared fixture already read back actual M7 Git HEAD and executes its
    # real native calculation. Only software data-root/calendar/clock adapters
    # are used, exactly as in that documented non-market fixture.
    with patch.object(old, '_native', lambda _root: recovery_case['native']), \
         patch.object(old, '_runtime', lambda _root: m.NATIVE), software_clock('2026-11-13'), \
         patch.object(old, 'datetime', m.datetime):
        old.build(recovery_case['data'], recovery_case['parent_path'], recovery_case['candidate'],
            '2026-11-13', scratch / 'package')
    cp = json.loads((scratch / 'package/checkpoint.json').read_bytes())
    payload = {role: (scratch / 'package' / cp[role]['path']).read_bytes() for role in m.ROLES}
    assert cp['recovery_controller']['source_sha256'] == LEGACY_SHA
    assert json.loads(payload['period'])[m.KEY]['controller_identity'] == cp['recovery_controller']
    return cp, payload


def test_exact_old_controller_earned_M7_proof_is_readable_after_upgrade(original_old_m7, tmp_path):
    cp, payload = original_old_m7
    before = copy.deepcopy((cp, payload))
    with software_clock('2026-11-13'):
        assert m.validate_recovery_payload(cp, payload)['sessions'] == ['2026-11-12', '2026-11-13']
        policy.validate_checkpoint_directory(package(tmp_path, cp, payload), policy.LOCAL_READ_POLICY)
    assert (cp, payload) == before


def test_old_controller_cannot_claim_M8_even_when_candidate_manifest_are_coherent(original_old_m7):
    cp, payload = as_runtime(original_old_m7, M8)
    with software_clock('2026-11-13'), pytest.raises(ValueError, match='controller source or native relationship'):
        m.validate_recovery_payload(cp, payload)


def test_new_controller_accepts_coherent_M8_identity_and_keeps_native_state(recovery_case, tmp_path):
    cp, payload = as_runtime((recovery_case['checkpoint'], recovery_case['payload']), M8)
    before = copy.deepcopy((cp, payload))
    with software_clock('2026-11-13'):
        assert m.reviewed_controller_identity(cp) == cp['recovery_controller']
        assert m.validate_recovery_payload(cp, payload)['rebalance_sessions'] == ['2026-11-12']
        policy.validate_checkpoint_directory(package(tmp_path, cp, payload), policy.LOCAL_READ_POLICY)
    assert (cp, payload) == before
    assert payload['history'] == recovery_case['payload']['history']
    assert payload['version_state'] == recovery_case['payload']['version_state']


@pytest.mark.parametrize('fault', ['candidate_M7', 'manifest_M7', 'unknown_runtime', 'mode_downgrade',
    'extra_controller_key', 'controller_identity_mismatch', 'missing_proof', 'downgrade_and_missing_proof',
    'remove_all_recovery_markers'])
def test_new_identity_cannot_cross_bind_or_downgrade(recovery_case, tmp_path, fault):
    cp, payload = as_runtime((recovery_case['checkpoint'], recovery_case['payload']), M8)
    period = json.loads(payload['period']);proof = period[m.KEY]
    if fault == 'candidate_M7':
        candidate = json.loads(m._unblob(proof['formal']['checkpoint']))
        candidate['strategy_sha'] = m.NATIVE
        proof['formal']['checkpoint'] = m._blob(m.encode(candidate))
    elif fault == 'manifest_M7':
        # Keep source hashes coherent; rejection must be release identity, not
        # merely a broken byte digest after changing the full manifest.
        manifest = json.loads(m._unblob(proof['formal']['manifest']));manifest['release_sha'] = m.NATIVE
        raw = m.encode(manifest);proof['formal']['manifest'] = m._blob(raw)
        candidate_period = json.loads(m._unblob(proof['formal']['period']))
        candidate_period['source_manifest_sha256'] = m.sha(raw)
        proof['formal']['period'] = m._blob(m.encode(candidate_period))
        candidate = json.loads(m._unblob(proof['formal']['checkpoint']))
        candidate['period']['sha256'] = proof['formal']['period']['sha256']
        proof['formal']['checkpoint'] = m._blob(m.encode(candidate))
        period['source_manifest_sha256'] = m.sha(raw)
    elif fault == 'unknown_runtime':
        cp, payload = as_runtime((cp, payload), '1' * 40)
        period = json.loads(payload['period'])
    elif fault == 'mode_downgrade':
        cp['handoff_mode'] = 'native_close_extends_previous'
    elif fault == 'extra_controller_key':
        cp['recovery_controller']['unreviewed_runtime_alias'] = M8
        proof['controller_identity'] = copy.deepcopy(cp['recovery_controller'])
    elif fault == 'controller_identity_mismatch':
        proof['controller_identity']['source_sha256'] = LEGACY_SHA
    elif fault == 'missing_proof':
        period.pop(m.KEY)
    elif fault == 'downgrade_and_missing_proof':
        period.pop(m.KEY);cp['handoff_mode'] = 'native_close_extends_previous'
    else:
        period.pop(m.KEY);cp.pop('recovery_controller')
        cp['handoff_mode'] = 'native_close_extends_previous'
    seal(cp, payload, period)
    with software_clock('2026-11-13'), pytest.raises(ValueError):
        policy.validate_checkpoint_directory(package(tmp_path, cp, payload), policy.LOCAL_READ_POLICY)


@pytest.mark.parametrize('fault', ['history_prefix', 'precision_bridge', 'native_numeric_state'])
def test_runtime_allowlist_keeps_history_precision_and_full_native_math_gates(recovery_case, fault):
    cp, payload = as_runtime((recovery_case['checkpoint'], recovery_case['payload']), M8)
    period = json.loads(payload['period']);proof = period[m.KEY]
    if fault == 'history_prefix':
        rows = m._rows(payload['history']);rows[0]['microcap'] = str(float(rows[0]['microcap']) + 1)
        from test_microcap_recovery_handoff import csv_bytes
        payload['history'] = csv_bytes(rows)
    elif fault == 'precision_bridge':
        period[m.BRIDGE] = dict(explicit_software_unreviewed_bridge=True)
    else:
        state = json.loads(m._unblob(proof['derived_state']))
        first = state['versions']['v2.0']
        field = next(key for key, value in first.items() if type(value) in (int, float))
        first[field] += 1
        payload['version_state'] = m.encode(state)
        proof['derived_state'] = m._blob(payload['version_state'])
    seal(cp, payload, period)
    with software_clock('2026-11-13'), pytest.raises(ValueError):
        m.validate_recovery_payload(cp, payload)
