import json
import subprocess
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import import_microcap_producer_inputs as inputs
import microcap_recovery_handoff as recovery
import member_checkpoint_policy as policy

ROOT = Path(__file__).resolve().parents[1]

def test_p6_reads_unchanged_calculation_contract_without_resigning_p5():
    p5 = inputs.producer_profile('microcap_local_producer_p5_20261116.json', inputs.P5_RUNTIME)
    p6 = inputs.producer_profile('microcap_local_producer_runtime.json', inputs.P6_RUNTIME)
    assert p5['calculation_sources'] == p6['calculation_sources']
    old = inputs.producer_profile('microcap_local_producer_p2_20261112.json', inputs.P2_RUNTIME)
    receipt = inputs.exact_p2_successor(inputs.P2_RUNTIME, old['calculation_sources'], p6['calculation_sources'], inputs.P6_RUNTIME)
    assert receipt['original_source_identity_preserved'] and receipt['native_proof_required']

def test_old_controller_can_read_old_proof_but_cannot_claim_m12():
    identity = dict(revision=recovery.CONTROLLER, source_sha256=recovery.LEGACY_PRE_M12_CONTROLLER_SHA)
    recovery.reviewed_controller_identity(dict(strategy_sha=recovery.M11, handoff_mode=recovery.MODE, recovery_controller=identity))
    with pytest.raises(ValueError):
        recovery.reviewed_controller_identity(dict(strategy_sha=recovery.M12, handoff_mode=recovery.MODE, recovery_controller=identity))

def test_new_reader_cannot_claim_m12_without_full_recovery_proof():
    with pytest.raises(ValueError, match='formal recovery proof route'):
        policy.validate_checkpoint_payload(dict(strategy_sha=recovery.M12, handoff_mode='native_close_extends_previous'), policy.LOCAL_READ_POLICY)

def test_previous_published_source_pointer_keeps_original_p5_identity():
    pointer = json.loads(subprocess.check_output(['git', '-C', str(ROOT), 'show', 'c2e25205e41a1564e365ff51d147a283fa946cb2:config/microcap_local_maintenance.json']))
    assert pointer['owner'] == 'local'
    assert pointer['scoped_inputs']['runtime_sha'] == inputs.P5_RUNTIME
