"""Regression: a holiday/manual replay must not resend yesterday's signal."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_ic_im_v1_4_delivery as gate  # noqa: E402
import check_ic_im_v1_4_result_day_delivery as result_gate  # noqa: E402


def result(market_date: str = "2026-09-24") -> dict[str, object]:
    return {
        "status": "ok",
        "strategy_revision": "r1",
        "build": gate.EXPECTED_BUILD,
        "delivery_revision": gate.EXPECTED_DELIVERY_REVISION,
        "publication_mode": "close_confirmed",
        "market_date": market_date,
        "digest": "a" * 64,
    }


def test_holiday_manual_run_finds_actual_prior_signal_day_marker():
    prefix = gate.marker_prefix(date(2026, 9, 24), "close_confirmed")
    artifacts = {"artifacts": [{"name": prefix + "837919794865", "expired": False}]}
    assert result_gate.should_send_for_result(result(), artifacts, correction=False) is False


def test_pending_signal_day_send_intent_blocks_uncertain_retry():
    prefix = gate.marker_prefix(date(2026, 9, 24), "close_confirmed")
    artifacts = {"artifacts": [{"name": prefix + "837919794865-send-intent", "expired": False}]}
    with pytest.raises(RuntimeError, match="uncertain"):
        result_gate.should_send_for_result(result(), artifacts, correction=False)


def test_explicit_correction_may_bypass_signal_day_marker():
    prefix = gate.marker_prefix(date(2026, 9, 24), "close_confirmed")
    artifacts = {"artifacts": [{"name": prefix + "837919794865", "expired": False}]}
    assert result_gate.should_send_for_result(result(), artifacts, correction=True) is True


def test_new_signal_day_without_marker_remains_eligible():
    assert result_gate.should_send_for_result(result("2026-09-28"), {"artifacts": []}, correction=False) is True
