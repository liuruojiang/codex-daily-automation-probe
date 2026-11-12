"""Structural role counterexamples; no market packet or producer SHA is relabeled."""
import copy
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import member_checkpoint_policy as policy
import restore_microcap_member_checkpoint as restore
import microcap_recovery_handoff as recovery

M5 = '1634a27c5a609a46ab35fc29ca2c65b94182671b'
M6 = '14e9a9fa6f0487f1d5547598435089c43f2f9fd1'
M7 = 'a7c1b9ac33454be4367880a0989dfc1466640503'
M8 = '95d896fe58cc7cb84c0fcb7bd76534f62c9430f4'
SOURCE_ONLY_PRODUCER = 'f6970d665d5c7a39b37c866cf13dc3d4cb8fdcc5'
P2_SOURCE_ONLY_PRODUCER = '3fe2e15ee9e3f2ee7e25bbc328479f1fd9910d36'
M7_UNDEPLOYED_CONTENT_CANDIDATE = 'c9657364696c263cd77d78f53088ff0d276252bd'


def known():
    return policy.load_read_policy(ROOT / 'config/microcap_member_local_runtime.json',
                                   policy.LOCAL_READ_POLICY['execution_strategy_sha'])


def test_reviewed_roles_preserve_execution_namespace_core_and_old_producers():
    value = known()
    assert value['execution_strategy_sha'] == 'ab214c20d92ca9bbda1dde7e447bb153e7f59028'
    assert value['checkpoint_strategy_sha'] == '4774966da38f0b5af18617f73cc67c39c9e41a8e'
    assert value['core_strategy_sha'] == 'ad4de441d35442616f91aa613146ffdf8cd479f3'
    assert value['rebalance_producer_shas'] == [M5, M6, M7, M8]
    assert len(value['rebalance_producer_shas']) == len(set(value['rebalance_producer_shas']))
    assert set(value['rebalance_producer_shas']).isdisjoint({value['execution_strategy_sha'],
        value['checkpoint_strategy_sha'], value['core_strategy_sha'], SOURCE_ONLY_PRODUCER,
        P2_SOURCE_ONLY_PRODUCER, M7_UNDEPLOYED_CONTENT_CANDIDATE})


@pytest.mark.parametrize('producer', [M5, M6, M7])
@pytest.mark.parametrize('mode', ['native_close_extends_previous', 'same_close_period_switch'])
def test_every_exact_reviewed_producer_keeps_original_lineage_without_payload_mutation(producer, mode):
    # A minimal structural value is not a native checkpoint acceptance claim.
    value = {'strategy_sha': producer, 'handoff_mode': mode, 'handoff_from': 'a' * 64}
    before = copy.deepcopy(value)
    policy.validate_checkpoint_payload(value, known())
    assert value == before


def recovery_value(mode=recovery.MODE, origin='a'*64):
    return dict(strategy_sha=M8,handoff_mode=mode,handoff_from=origin,
        recovery_controller=dict(revision=recovery.CONTROLLER,
            source_sha256=recovery.sha(Path(recovery.__file__).read_bytes().replace(b'\r\n',b'\n'))))


@pytest.mark.parametrize('mode',[recovery.MODE,'native_close_extends_previous','same_close_period_switch'])
def test_M8_uses_only_its_formal_recovery_route(mode):
    # Structural identity only; complete native recovery payload is separately tested.
    value=recovery_value(mode);before=copy.deepcopy(value)
    if mode==recovery.MODE:
        policy.validate_checkpoint_payload(value,known())
    else:
        with pytest.raises(ValueError,match='formal recovery proof route'):
            policy.validate_checkpoint_payload(value,known())
    assert value==before


@pytest.mark.parametrize('producer', policy.LOCAL_READ_POLICY['rebalance_producer_shas'])
@pytest.mark.parametrize('mode,origin', [
    (None, 'a' * 64), ('', 'a' * 64), ('preview', 'a' * 64),
    ('native_close_extends_previous', None), ('native_close_extends_previous', ''),
    ('native_close_extends_previous', 'a' * 63), ('native_close_extends_previous', 'a' * 65),
    ('native_close_extends_previous', 'A' * 64), ('native_close_extends_previous', 'g' * 64),
    ('same_close_period_switch', '123'), ('same_close_period_switch', 1)])
def test_reviewed_marker_cannot_borrow_an_unknown_mode_or_malformed_parent(producer, mode, origin):
    value={'strategy_sha':producer,'handoff_mode':mode,'handoff_from':origin}
    if producer==M8:
        # Exercise the actual parent/type gate under M8's valid route; normal
        # M8 routes are independently rejected above, before lineage checks.
        selected=recovery.MODE if mode in ('native_close_extends_previous','same_close_period_switch') else mode
        value=recovery_value(selected,origin)
    error='formal recovery proof route' if producer==M8 and value['handoff_mode']!=recovery.MODE else 'handoff lineage'
    with pytest.raises(ValueError, match=error):
        policy.validate_checkpoint_payload(value,known())


@pytest.mark.parametrize('wrong_role', [SOURCE_ONLY_PRODUCER, P2_SOURCE_ONLY_PRODUCER,
    M7_UNDEPLOYED_CONTENT_CANDIDATE,
    'ab214c20d92ca9bbda1dde7e447bb153e7f59028',
    '4774966da38f0b5af18617f73cc67c39c9e41a8e'])
def test_real_but_wrong_role_SHA_cannot_become_a_rebalance_producer(wrong_role):
    with pytest.raises(ValueError, match='core identity'):
        policy.validate_checkpoint_payload({'strategy_sha': wrong_role,
            'handoff_mode': 'native_close_extends_previous', 'handoff_from': 'a' * 64}, known())


@pytest.mark.parametrize('fault', ['append_source_role', 'drop_M5', 'reverse_roles', 'duplicate_role'])
def test_external_config_cannot_expand_or_redefine_reviewed_roles_before_network(tmp_path, monkeypatch, fault):
    altered = copy.deepcopy(policy.LOCAL_READ_POLICY)
    if fault == 'append_source_role': altered['rebalance_producer_shas'].append(SOURCE_ONLY_PRODUCER)
    elif fault == 'drop_M5': altered['rebalance_producer_shas'].remove(M5)
    elif fault == 'reverse_roles': altered['rebalance_producer_shas'].reverse()
    else: altered['rebalance_producer_shas'].append(M6)
    path = tmp_path / 'unreviewed-policy.json'
    path.write_text(json.dumps(altered), encoding='utf-8')
    monkeypatch.setattr(restore, 'fetch_latest', lambda *a, **k: pytest.fail('Unreviewed roles must not discover artifacts'))
    out = tmp_path / 'not-created'
    monkeypatch.setattr(sys, 'argv', ['restore', '--strategy-sha', policy.LOCAL_READ_POLICY['execution_strategy_sha'],
        '--checkpoint-read-policy', str(path), '--out', str(out)])
    with pytest.raises(ValueError, match='Unreviewed'):
        restore.main()
    assert not out.exists()
