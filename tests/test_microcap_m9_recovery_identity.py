"""M9 software identity envelopes and genuine historical controller execution.

Native math is the existing real M7 calculation fixture, with its documented
software data/calendar/clock adapters.  Runtime envelopes are explicitly
software-only: these tests do not earn an M8/M9 market gate or source freshness.
The 5d controller is loaded from its exact Git bytes and actually writes the
historical proof; a current proof is never relabelled as an old controller.
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
from test_microcap_m8_recovery_identity import as_runtime, package, seal, original_old_m7

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import microcap_recovery_handoff as m
import member_checkpoint_policy as policy
import microcap_scoped_inputs as scoped_inputs
import microcap_notice_prefix as prefix
import maintain_microcap_local as maintenance
import microcap_scoped_settings as settings

OLD_AUTO = '5d27efb1986837b1b66083521ecf2ac543aaa650'
OLD_CONTROLLER = '8a52b8d9b1c6caaaba868cd447073f9d42c432d3d131e4646bfe9442814a2e4b'
M9 = '906eca8a5dfede7fa1674fb17cd3c6ce12205e6d'
P3 = '3344e403ff01e17ef45b745a704a240e0917720b'


@pytest.fixture(scope='module')
def old_5d_controller(tmp_path_factory):
    # CI fetches this fixed object before pytest. No fetching/network or source
    # substitution takes place inside the executable regression.
    raw = subprocess.check_output(['git', '-C', str(ROOT), 'show',
                                   OLD_AUTO + ':scripts/microcap_recovery_handoff.py'])
    assert m.sha(raw.replace(b'\r\n', b'\n')) == OLD_CONTROLLER
    source = tmp_path_factory.mktemp('exact_5d_controller') / 'old_handoff.py'
    source.write_bytes(raw)
    spec = importlib.util.spec_from_file_location('exact_5d_recovery_controller', source)
    old = importlib.util.module_from_spec(spec); spec.loader.exec_module(old)
    assert old.REVIEWED_NATIVES == {m.NATIVE, m.M8} and M9 not in old.REVIEWED_NATIVES
    return old


@pytest.fixture(scope='module', params=['M7', 'M8'])
def genuinely_executed_5d_proof(request, recovery_case, old_5d_controller, tmp_path_factory):
    runtime = m.NATIVE if request.param == 'M7' else m.M8
    old = old_5d_controller
    temp = tmp_path_factory.mktemp('old_5d_software_' + request.param)
    data = temp / 'software-formal'
    # Copy only the tiny software fixture, never a production/whole workspace.
    for path in recovery_case['data'].rglob('*'):
        if path.is_file():
            destination = data / path.relative_to(recovery_case['data'])
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(path.read_bytes())
    formal = data / 'outputs/top100_delivery_manifest.json'
    manifest = json.loads(formal.read_bytes())
    assert manifest['explicit_software_formal_envelope'] is True
    manifest['release_sha'] = runtime; formal.write_bytes(old.encode(manifest))
    # This is an explicit runtime-envelope adapter around unchanged real native
    # fixture math, not a claim that the underlying native checkout is M8.
    with software_clock('2026-11-13'), patch.object(old, 'datetime', m.datetime), \
         patch.object(old, '_runtime', lambda _root: runtime), \
         patch.object(old, '_native', lambda _root: recovery_case['native']):
        old.bootstrap_formal_candidate(data, '2026-11-13', temp / 'candidate')
        old.build(data, recovery_case['parent_path'], temp / 'candidate/checkpoint.json',
                  '2026-11-13', temp / 'earned-by-old-source')
    cp = json.loads((temp / 'earned-by-old-source/checkpoint.json').read_bytes())
    payload = {role: (temp / 'earned-by-old-source' / cp[role]['path']).read_bytes() for role in m.ROLES}
    assert cp['strategy_sha'] == runtime
    assert cp['recovery_controller']['source_sha256'] == OLD_CONTROLLER
    assert json.loads(payload['period'])[m.KEY]['controller_identity'] == cp['recovery_controller']
    return cp, payload


def test_new_M9_full_software_payload_and_local_reader_keep_original_states(recovery_case, tmp_path):
    cp, payload = as_runtime((recovery_case['checkpoint'], recovery_case['payload']), M9)
    before = copy.deepcopy((cp, payload))
    with software_clock('2026-11-13'):
        result = m.validate_recovery_payload(cp, payload)
        assert result['sessions'] == ['2026-11-12', '2026-11-13']
        assert result['rebalance_sessions'] == ['2026-11-12']
        policy.validate_checkpoint_directory(package(tmp_path, cp, payload), policy.LOCAL_READ_POLICY)
    assert (cp, payload) == before
    for role in ('history', 'turnover', 'version_state', 'prices'):
        assert payload[role] == recovery_case['payload'][role]


def test_exact_5d_controller_M7_and_M8_proofs_remain_readable_unchanged(genuinely_executed_5d_proof, tmp_path):
    cp, payload = genuinely_executed_5d_proof
    before = copy.deepcopy((cp, payload))
    with software_clock('2026-11-13'):
        assert m.validate_recovery_payload(cp, payload)['rebalance_sessions'] == ['2026-11-12']
        policy.validate_checkpoint_directory(package(tmp_path, cp, payload), policy.LOCAL_READ_POLICY)
    assert (cp, payload) == before


def test_old_5d_proof_cannot_be_upgraded_to_M9_by_coherent_rebinding(genuinely_executed_5d_proof):
    cp, payload = as_runtime(genuinely_executed_5d_proof, M9)
    with software_clock('2026-11-13'), pytest.raises(ValueError, match='controller source or native relationship'):
        m.validate_recovery_payload(cp, payload)


def test_original_eea_M7_controller_cannot_claim_M9(original_old_m7):
    cp, payload = as_runtime(original_old_m7, M9)
    with software_clock('2026-11-13'), pytest.raises(ValueError, match='controller source or native relationship'):
        m.validate_recovery_payload(cp, payload)


@pytest.mark.parametrize('controller', [m.LEGACY_M7_CONTROLLER_SHA, OLD_CONTROLLER, '0' * 64])
def test_M9_rejects_old_or_unknown_controller_with_every_dependent_hash_rebound(recovery_case, controller):
    cp, payload = as_runtime((recovery_case['checkpoint'], recovery_case['payload']), M9)
    period = json.loads(payload['period'])
    cp['recovery_controller']['source_sha256'] = controller
    period[m.KEY]['controller_identity'] = copy.deepcopy(cp['recovery_controller'])
    seal(cp, payload, period)
    with software_clock('2026-11-13'), pytest.raises(ValueError, match='controller source or native relationship'):
        m.validate_recovery_payload(cp, payload)


@pytest.mark.parametrize('fault', ['mode_downgrade', 'all_markers_removed', 'candidate_M8', 'manifest_M8', 'native_state'])
def test_M9_envelope_cannot_drop_proof_cross_bind_or_change_native_state(recovery_case, tmp_path, fault):
    cp, payload = as_runtime((recovery_case['checkpoint'], recovery_case['payload']), M9)
    period = json.loads(payload['period']); proof = period[m.KEY]
    if fault == 'mode_downgrade':
        cp['handoff_mode'] = 'native_close_extends_previous'
    elif fault == 'all_markers_removed':
        period.pop(m.KEY); cp.pop('recovery_controller')
        cp['handoff_mode'] = 'native_close_extends_previous'
    elif fault == 'candidate_M8':
        candidate = json.loads(m._unblob(proof['formal']['checkpoint']))
        candidate['strategy_sha'] = m.M8
        proof['formal']['checkpoint'] = m._blob(m.encode(candidate))
    elif fault == 'manifest_M8':
        formal = proof['formal']
        manifest = json.loads(m._unblob(formal['manifest'])); manifest['release_sha'] = m.M8
        raw = m.encode(manifest); formal['manifest'] = m._blob(raw)
        candidate_period = json.loads(m._unblob(formal['period']))
        candidate_period['source_manifest_sha256'] = m.sha(raw)
        formal['period'] = m._blob(m.encode(candidate_period))
        candidate = json.loads(m._unblob(formal['checkpoint']))
        candidate['period']['sha256'] = formal['period']['sha256']
        formal['checkpoint'] = m._blob(m.encode(candidate))
        period['source_manifest_sha256'] = m.sha(raw)
    else:
        state = json.loads(m._unblob(proof['derived_state']))
        first = state['versions']['v2.0']
        field = next(key for key, value in first.items() if type(value) in (int, float))
        first[field] += 1
        payload['version_state'] = m.encode(state); proof['derived_state'] = m._blob(payload['version_state'])
    seal(cp, payload, period)
    with software_clock('2026-11-13'), pytest.raises(ValueError):
        policy.validate_checkpoint_directory(package(tmp_path, cp, payload), policy.LOCAL_READ_POLICY)


def test_M9_configs_add_only_consumer_role_and_keep_source_P3_and_seed():
    old = json.loads(subprocess.check_output(['git', '-C', str(ROOT), 'show', OLD_AUTO + ':config/microcap_scoped_rebalance.json']))
    current = settings.load(ROOT / 'config/microcap_scoped_rebalance.json', require_seed=True)
    assert current['runtime_sha'] == M9
    assert current['producer_sha'] == maintenance.RUNTIME == P3
    old['runtime_sha'] = M9
    assert current == old
    old_policy = json.loads(subprocess.check_output(['git', '-C', str(ROOT), 'show', OLD_AUTO + ':config/microcap_member_local_runtime.json']))
    expected = copy.deepcopy(old_policy); expected['rebalance_producer_shas'].append(M9)
    assert policy.load_read_policy(ROOT / 'config/microcap_member_local_runtime.json') == expected
    assert M9 in m.REVIEWED_NATIVES and M9 in scoped_inputs.REVIEWED_CONSUMERS
    assert M9 in prefix.CONSUMER_READERS and M9 not in prefix.PRODUCER_READERS


def test_M9_cloud_owner_does_not_enter_the_AB214_local_notice_selector(tmp_path, monkeypatch):
    state = tmp_path / 'state'; root = state / 'runtime'; root.mkdir(parents=True)
    protected = tmp_path / 'software-M9'; protected.mkdir()
    out = state / 'selection'
    calls = []
    def git(argv, **kwargs):
        calls.append(list(map(str, argv)))
        assert argv == ['git', '-C', str(protected.resolve()), 'rev-parse', 'HEAD']
        return M9 + '\n'
    def forbidden_native(*args, **kwargs):
        pytest.fail('Cloud M9 must use its separate private forward path, not the AB214 selector')
    monkeypatch.setattr(maintenance.subprocess, 'check_output', git)
    before = sorted(str(p) for p in tmp_path.rglob('*'))
    result = maintenance.select_local_notice_forward(root=root, protected_root=protected,
        state=state, symbols=[], target='2026-11-12', out=out, native=forbidden_native)
    assert result['status'] == 'not_applicable_to_local_notice_forward'
    assert result['verification_consumer_runtime'] == M9
    assert result['target_freshness_granted'] is False and result['current_signal_ready'] is False
    assert len(calls) == 1 and sorted(str(p) for p in tmp_path.rglob('*')) == before


def test_notice_runtime_role_accepts_M9_only_as_reviewed_consumer(tmp_path, monkeypatch):
    def git(argv, **kwargs):
        return M9 + '\n' if argv[3:] == ['rev-parse', 'HEAD'] else ''
    monkeypatch.setattr(prefix.subprocess, 'check_output', git)
    assert prefix.runtime_role(tmp_path, prefix.CONSUMER) == tmp_path.resolve()
    with pytest.raises(prefix.NoticePrefixError, match='git_role_differs'):
        prefix.runtime_role(tmp_path, prefix.PRODUCER)
