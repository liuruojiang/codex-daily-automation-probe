"""Producer-mandated recovery proof across transport, cache and publication.

The small formal fixture executes reviewed M7 math. M8/M9/M10 envelopes below are
explicit software identities, not new prices, production gates or publications.
"""
import copy
from datetime import datetime
import importlib.util
import io
import json
from pathlib import Path
import sys
import zipfile

import pytest

from test_microcap_recovery_handoff import recovery_case, software_clock
from test_microcap_m8_recovery_identity import as_runtime, package, seal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import microcap_recovery_handoff as recovery
import member_checkpoint_policy as policy
import restore_microcap_member_checkpoint as restore
import microcap_member_report_contract as contract
import build_microcap_member_digest as digest


def archive_bytes(checkpoint, payload):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('checkpoint.json', recovery.encode(checkpoint))
        for role in recovery.ROLES:
            archive.writestr(checkpoint[role]['path'], payload[role])
    return stream.getvalue()


def downgraded(source, producer, mode):
    checkpoint, payload = as_runtime(source, producer)
    period = json.loads(payload['period'])
    period.pop(recovery.KEY)
    checkpoint.pop('recovery_controller')
    checkpoint['handoff_mode'] = mode
    return seal(checkpoint, payload, period)


@pytest.mark.parametrize('producer', [recovery.M8, recovery.M9, recovery.M10])
@pytest.mark.parametrize('mode', [None, recovery.MODE, 'native_close_extends_previous',
                                'same_close_period_switch', 'unsupported_unproved_mode'])
def test_producer_cannot_remove_all_proof_markers_at_any_reader_boundary(recovery_case, tmp_path, producer, mode):
    checkpoint, payload = downgraded((recovery_case['checkpoint'], recovery_case['payload']), producer, mode)
    before = copy.deepcopy((checkpoint, payload))
    raw = archive_bytes(checkpoint, payload)
    path = package(tmp_path, checkpoint, payload)
    with software_clock('2026-11-13'):
        for check in (lambda: recovery.validate_recovery_payload(checkpoint, payload),
                      lambda: restore.checkpoint_identity(raw),
                      lambda: policy.validate_checkpoint_core(raw, None),
                      lambda: policy.validate_checkpoint_core(raw, policy.LOCAL_READ_POLICY),
                      lambda: policy.validate_checkpoint_directory(path, None),
                      lambda: policy.validate_checkpoint_directory(path, policy.LOCAL_READ_POLICY)):
            with pytest.raises(ValueError, match='proof|recovery|producer'):
                check()
    assert (checkpoint, payload) == before


@pytest.mark.parametrize('producer', [recovery.NATIVE, recovery.M8, recovery.M9])
def test_original_605_controller_keeps_exact_preceding_native_relationships(producer):
    checkpoint = dict(strategy_sha=producer, handoff_mode=recovery.MODE,
        recovery_controller=dict(revision=recovery.CONTROLLER, source_sha256=recovery.LEGACY_M9_CONTROLLER_SHA))
    before = copy.deepcopy(checkpoint)
    assert recovery.reviewed_controller_identity(checkpoint) == checkpoint['recovery_controller']
    assert checkpoint == before


@pytest.mark.parametrize('producer', [recovery.M10, recovery.CORE, '1634a27c5a609a46ab35fc29ca2c65b94182671b', '0' * 40])
def test_original_605_controller_does_not_approve_other_native_producers(producer):
    checkpoint = dict(strategy_sha=producer, handoff_mode=recovery.MODE,
        recovery_controller=dict(revision=recovery.CONTROLLER, source_sha256=recovery.LEGACY_M9_CONTROLLER_SHA))
    with pytest.raises(ValueError, match='controller source or native relationship'):
        recovery.reviewed_controller_identity(checkpoint)


@pytest.mark.parametrize('producer', [recovery.M8, recovery.M9, recovery.M10])
def test_new_recovery_build_cannot_launder_an_unproved_parent(recovery_case, tmp_path, producer):
    # Only the declared producer is adversarial software input; the real saved
    # D11 data bytes and all their dependency hashes remain unchanged.
    checkpoint = json.loads(recovery_case['parent_path'].read_bytes())
    checkpoint['strategy_sha'] = producer
    checkpoint.pop('recovery_controller', None)
    checkpoint['handoff_mode'] = 'native_close_extends_previous'
    payload = {role: (recovery_case['parent_path'].parent / checkpoint[role]['path']).read_bytes()
               for role in recovery.ROLES}
    parent = package(tmp_path, checkpoint, payload)
    with software_clock('2026-11-13'), pytest.raises(ValueError, match='formal recovery proof route'):
        recovery.build(recovery_case['data'], parent, recovery_case['candidate'],
                       '2026-11-13', tmp_path / 'must-not-publish')
    assert not (tmp_path / 'must-not-publish').exists()


@pytest.mark.parametrize('producer', [recovery.NATIVE, recovery.M8, recovery.M9, recovery.M10])
def test_original_239_controller_keeps_its_exact_four_reviewed_relationships(producer):
    checkpoint = dict(strategy_sha=producer, handoff_mode=recovery.MODE,
        recovery_controller=dict(revision=recovery.CONTROLLER, source_sha256=recovery.LEGACY_M10_CONTROLLER_SHA))
    assert recovery.reviewed_controller_identity(checkpoint) == checkpoint['recovery_controller']


def load_fixture(name, module_name):
    spec = importlib.util.spec_from_file_location(module_name,
        ROOT / 'tests/fixtures/member_native_M6_R12' / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('fault', [None, 'markers_stripped', 'proof_missing', 'unknown_controller'])
def test_exact_477_saved_close_cannot_bypass_publication_proof(recovery_case, tmp_path, monkeypatch, fault):
    checkpoint, payload = as_runtime((recovery_case['checkpoint'], recovery_case['payload']), recovery.M9)
    if fault == 'markers_stripped':
        checkpoint, payload = downgraded((recovery_case['checkpoint'], recovery_case['payload']),
            recovery.M9, 'native_close_extends_previous')
    elif fault is not None:
        period = json.loads(payload['period'])
        if fault == 'proof_missing':
            period.pop(recovery.KEY)
        else:
            checkpoint['recovery_controller']['source_sha256'] = '0' * 64
            period[recovery.KEY]['controller_identity'] = copy.deepcopy(checkpoint['recovery_controller'])
        seal(checkpoint, payload, period)
    source = tmp_path / 'source'; source.mkdir()
    path = package(source, checkpoint, payload)
    original = {p.name: p.read_bytes() for p in source.iterdir()}
    verifier = load_fixture('original477_member_period_handoff.py', 'proof_gate_exact477_verifier')
    daily = load_fixture('original477_member_daily_signal.py', 'proof_gate_exact477_daily')
    monkeypatch.setitem(sys.modules, 'scripts.member_period_handoff', verifier)
    result = daily.saved_close_result(path, '2026-11-13', tmp_path / 'daily',
        now=datetime.fromisoformat('2026-11-13T16:30:00+08:00'))
    context = digest.saved_context(tmp_path / 'daily', path, '2026-11-13', result)
    monkeypatch.setattr(digest, 'now_bj', lambda: datetime.fromisoformat('2026-11-13T16:30:01+08:00'))
    with software_clock('2026-11-13'):
        if fault is None:
            assert contract.saved_close_identity(result, context, '2026-11-13', digest.REVISIONS)
            assert set(contract.final_signal_files(result, tmp_path / 'daily', '2026-11-13',
                digest.REVISIONS, context=context)) == set(digest.REVISIONS)
        else:
            for check in (lambda: contract.saved_close_identity(result, context, '2026-11-13', digest.REVISIONS),
                          lambda: contract.final_signal_files(result, tmp_path / 'daily', '2026-11-13',
                              digest.REVISIONS, context=context),
                          lambda: digest.render(result, expected_date='2026-11-13', context=context)):
                with pytest.raises(ValueError, match='proof|recovery|producer|controller'):
                    check()
    assert {p.name: p.read_bytes() for p in source.iterdir()} == original
