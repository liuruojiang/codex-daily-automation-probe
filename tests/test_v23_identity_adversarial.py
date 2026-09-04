"""V2.3 email identity cannot contradict its fixed selected parameters."""
import importlib.util
from pathlib import Path

import pytest


_path = Path(__file__).resolve().parents[1] / "scripts" / "build_microcap_realtime_digest.py"
_spec = importlib.util.spec_from_file_location("v23_identity_digest", _path)
digest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(digest)


def _identity():
    expected = digest.STRATEGY_IDENTITIES["v2.3"]
    return {key: str(value) for section in expected.values() for key, value in section.items()}


@pytest.mark.parametrize("field,bad", [
    ("r2_gate_enabled", "True"), ("signal_spread_hedge_ratio", ".8"),
    ("momentum_gap_entry_threshold", ".9"), ("momentum_gap_exit_buffer", ".09"),
    ("cash_day_yield_enabled", "True"), ("financing_enabled", "True"),
])
def test_missing_and_contradictory_v23_parameter_rejected(field, bad):
    row = _identity()
    assert digest.validate_strategy_identity("v2.3", row)[0]
    row[field] = bad
    assert not digest.validate_strategy_identity("v2.3", row)[0]
    row.pop(field)
    assert not digest.validate_strategy_identity("v2.3", row)[0]


@pytest.mark.parametrize("field", ["signal_spread_hedge_ratio", "momentum_gap_entry_threshold",
                                   "momentum_gap_exit_buffer"])
@pytest.mark.parametrize("bad", ["nan", "inf", "-inf"])
def test_v23_numeric_identity_is_finite(field, bad):
    row = _identity()
    row[field] = bad
    assert not digest.validate_strategy_identity("v2.3", row)[0]
