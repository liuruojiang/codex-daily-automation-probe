"""Offline counterexamples for the real IC/IM mail-packaging boundary.

The fixture is explicitly synthetic and copies the observed 2026-09-30
producer schema.  It is not a market observation or a trading target.
"""
from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_ic_im_v1_4_digest as digest
import check_ic_im_v1_4_delivery as gate


def valid_payload():
    payload = {
        "status": "ok", "strategy_revision": "r1", "build": gate.FIX9_BUILD,
        "signal_build": gate.FIX9_BUILD,
        "signal_rule_revision": "ic_im_v1_4_ordinaryput_open_ic_seller_mom120_20260929_v1",
        "delivery_revision": gate.FIX9_DELIVERY_REVISION,
        "publication_mode": "close_confirmed", "market_date": "2026-09-30",
        "completed_day": "2026-09-30", "verified_day": "2026-09-30",
        "next_trade_day": "2026-10-08", "sequence": 9, "digest": "a" * 64,
        "signals": {},
    }
    for product in ("IC", "IM"):
        payload["signals"][product] = {
            "product": product, "strategy_version": "1.4", "strategy_revision": "r1",
            "market_date": "2026-09-30", "next_trade_date": "2026-10-08",
            "v14_build_id": payload["build"], "v14_rule_revision": payload["signal_rule_revision"],
            "close_confirmed": True, "market_phase": "收盘后",
            "core_units_current": .5, "core_units_target": .5,
            "momentum_current_weight": 0., "momentum_next_weight": 0.,
            "momentum_units_current": 0., "momentum_units_target": 0., "momentum_units_change": 0.,
            "grid_current": 0., "grid_target": 0.,
            "total_units_current": .5, "total_units_target": .5, "total_units_change": 0.,
            "v14_route_state": "future", "v14_short_put_qty_normalized": 0.,
            "v14_short_put_contract": None, "v14_roll_pending": False,
            "v14_settlement_pending": False, "v14_profit_pending": False,
            "v14_profit_execution_day": None, "v14_profit_trigger_day": None,
            "v14_profit_old_contract": None, "v14_profit_old_qty": 0.,
            "v14_profit_reentry_contract": None, "v14_profit_reentry_qty": 0.,
            "v14_ordinary_put_pending": None, "v14_ordinary_put_plan_status": "not_required",
            "call_target_qty_normalized": 0., "call_target_contract": None,
            "call_target_expiry": None, "call_target_strike": None,
            "call_action": "HOLD", "call_has_position": False,
        }
    payload["signals"]["IC"].update(
        put_current_core_qty=10, put_target_core_qty=10,
        put_current_momentum_qty=0, put_target_momentum_qty=0,
        put_current_grid_qty=0, put_target_grid_qty=0,
        put_current_total_qty=10, put_target_total_qty=10,
        put_current_contract="510500P2612M07500", put_target_contract="510500P2612M07500",
        put_current_security_id="10012099", put_target_security_id="10012099",
        v14_core_put_current_contract="510500P2612M07500",
        v14_core_put_contract="510500P2612M07500", v14_core_put_security_id="10012099",
        v14_core_put_qty=10,
    )
    payload["signals"]["IM"].update(
        core_put_current_qty_normalized=1.5, core_put_target_qty_normalized=1.5,
        momentum_put_current_qty_normalized=0., momentum_put_target_qty_normalized=0.,
        total_put_current_qty_normalized=1.5, total_put_target_qty_normalized=1.5,
        core_put_current_contract="MO2612-P-7600", core_put_target_contract="MO2612-P-7600",
        momentum_put_current_contract=None, momentum_put_target_contract=None,
    )
    return payload


def change_next_day(payload, day):
    payload["next_trade_day"] = day
    for signal in payload["signals"].values():
        signal["next_trade_date"] = day


def valid_fix11_payload():
    payload = valid_payload()
    payload.update(build=gate.FIX11_BUILD, signal_build=gate.FIX11_BUILD,
                   delivery_revision=gate.FIX11_DELIVERY_REVISION,
                   signal_rule_revision="ic_im_v1_4_ic_csi500_ma105_w16_abs40_static_20261008_v1",
                   market_date="2026-10-08", completed_day="2026-10-08",
                   verified_day="2026-10-08", next_trade_day="2026-10-09",
                   grid_policy_revision="ic_im_or_fear25_paired_exit50_20261008_v1")
    for signal in payload["signals"].values():
        signal.update(market_date="2026-10-08", next_trade_date="2026-10-09",
                      v14_build_id=payload["build"], v14_rule_revision=payload["signal_rule_revision"],
                      grid_policy_revision=payload["grid_policy_revision"],
                      grid_entry_source_current="none", grid_entry_source_target="none",
                      fear_greed_index=40., fear_data_date="2026-10-08",
                      fear_data_status="same_day_post_close", fear_csv_sha256="b"*64)
    payload["signals"]["IC"].update(momentum_abs=.02, momentum_abs_days=40,
                                      momentum_abs_on=True, momentum_abs_debounce_active=False)
    payload["signals"]["IM"]["v14_core_put_qty"] = 0.
    return payload


def ordinary_plan(payload):
    signal = payload["signals"]["IM"]
    signal["v14_ordinary_put_plan_status"] = "scheduled_t_plus_1_open"
    signal["core_put_target_contract"] = "MO2703-P-7600"
    signal["v14_ordinary_put_pending"] = {
        "product": "IM", "signal_day": payload["market_date"],
        "execution_day": payload["next_trade_day"],
        "legs": {
            "core": {"changed": True, "old_contract": "MO2612-P-7600",
                     "old_qty": 1.5, "new_contract": "MO2703-P-7600", "new_qty": 1.5},
            "momentum": {"changed": False, "old_contract": None, "old_qty": 0.,
                         "new_contract": None, "new_qty": 0.},
        },
    }
    return signal


def test_synthetic_schema_control_is_accepted():
    digest.validate_success_payload(valid_payload())


def test_complete_current_fix11_schema_control_is_accepted():
    digest.validate_success_payload(valid_fix11_payload())


@pytest.mark.parametrize("product,field", [
    ("IC", "core_units_current"), ("IM", "momentum_units_target"),
    ("IM", "total_units_change"), ("IC", "momentum_units_change"),
    ("IC", "put_current_core_qty"), ("IC", "put_target_grid_qty"),
    ("IM", "core_put_target_qty_normalized"), ("IM", "total_put_current_qty_normalized"),
    ("IC", "v14_core_put_qty"), ("IM", "v14_ordinary_put_pending"),
    ("IC", "v14_profit_pending"), ("IM", "v14_roll_pending"),
])
def test_current_fix11_required_fields_cannot_be_deleted_to_bypass_checks(product, field):
    payload = valid_fix11_payload()
    del payload["signals"][product][field]
    with pytest.raises(ValueError):
        digest.validate_success_payload(payload)


@pytest.mark.parametrize("case", ["normal", "holiday", "quantity", "missing_field"])
def test_marker_cli_requires_full_delivery_contract_before_writing(tmp_path, monkeypatch, case):
    import json
    import prepare_ic_im_v1_4_marker as marker
    payload = valid_fix11_payload()
    if case == "holiday":
        change_next_day(payload, "2026-10-10")
    elif case == "quantity":
        payload["signals"]["IM"]["core_put_target_qty_normalized"] = -1.5
    elif case == "missing_field":
        del payload["signals"]["IC"]["core_units_current"]
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(payload), encoding="utf-8")
    marker_dir = tmp_path / "marker"
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setattr(sys, "argv", ["prepare", "--result", str(result_path), "--marker-dir", str(marker_dir)])
    if case == "normal":
        assert marker.main() == 0
        assert (marker_dir / "delivery.json").is_file()
    else:
        with pytest.raises(ValueError):
            marker.main()
        assert not marker_dir.exists()


def test_valid_ordinary_plan_control_is_accepted():
    payload = valid_payload()
    ordinary_plan(payload)
    digest.validate_success_payload(payload)


def test_legitimate_weight_above_one_control_is_accepted():
    payload = valid_payload()
    signal = payload["signals"]["IM"]
    signal.update(momentum_current_weight=2.5, momentum_next_weight=2.,
                  momentum_units_current=1.25, momentum_units_target=1.,
                  momentum_units_change=-.25, total_units_current=1.75,
                  total_units_target=1.5, total_units_change=-.25)
    digest.validate_success_payload(payload)


def test_valid_profit_plan_control_is_accepted():
    payload = valid_payload()
    payload["signals"]["IM"].update(
        v14_profit_pending=True, v14_profit_trigger_day="2026-09-30",
        v14_profit_execution_day="2026-10-08",
        v14_profit_old_contract="MO2612-P-7600", v14_profit_old_qty=1.5,
        v14_profit_reentry_contract="MO2703-P-7600", v14_profit_reentry_qty=1.5,
    )
    digest.validate_success_payload(payload)


@pytest.mark.parametrize("case", ["unknown_route", "profit_recovery", "profit_monthly_confirmed", "orphan_roll", "orphan_settlement"])
def test_independently_found_lifecycle_combinations_are_rejected(case):
    payload = valid_fix11_payload()
    signal = payload["signals"]["IM"]
    if case.startswith("profit"):
        signal.update(v14_profit_pending=True, v14_profit_trigger_day="2026-10-08",
                      v14_profit_execution_day="2026-10-09",
                      v14_profit_old_contract="MO2612-P-7600", v14_profit_old_qty=1.5,
                      v14_profit_reentry_contract="MO2703-P-7600", v14_profit_reentry_qty=1.5)
        if case == "profit_recovery":
            signal["v14_route_state"] = "recovery_future"
        else:
            signal.update(option_monthly_reset_due=True, v14_core_put_target_entry_premium=100.)
    elif case == "orphan_roll":
        signal["v14_roll_pending"] = True
    elif case == "orphan_settlement":
        signal["v14_settlement_pending"] = True
    else:
        signal["v14_route_state"] = "bogus"
    with pytest.raises(ValueError):
        digest.validate_success_payload(payload)


def test_unpriced_monthly_maintenance_keeps_auditable_profit_pending():
    payload = valid_fix11_payload()
    payload["signals"]["IM"].update(
        v14_profit_pending=True, v14_profit_trigger_day="2026-10-08",
        v14_profit_execution_day="2026-10-09",
        v14_profit_old_contract="MO2612-P-7600", v14_profit_old_qty=1.5,
        v14_profit_reentry_contract="MO2703-P-7600", v14_profit_reentry_qty=1.5,
        option_monthly_reset_due=True, v14_core_put_target_entry_premium=None,
    )
    digest.validate_success_payload(payload)


def test_independent_calendar_refuses_unsupported_year_even_on_weekend():
    from datetime import date
    from ic_im_v1_4_delivery_contract import is_session
    with pytest.raises(ValueError, match="does not cover"):
        is_session(date(2027, 1, 2))


@pytest.mark.parametrize("case", ["holiday_execution", "skipped_session", "closed_signal", "unsupported_year", "stale_realtime_anchor"])
def test_self_consistent_dates_must_pass_independent_exchange_calendar(case):
    payload = valid_payload()
    if case == "holiday_execution":
        change_next_day(payload, "2026-10-01")
    elif case == "skipped_session":
        change_next_day(payload, "2026-10-09")
    elif case == "closed_signal":
        payload.update(market_date="2026-10-01", completed_day="2026-10-01", verified_day="2026-10-01")
        for signal in payload["signals"].values():
            signal["market_date"] = "2026-10-01"
    elif case == "unsupported_year":
        change_next_day(payload, "2027-01-04")
    else:
        payload.update(publication_mode="realtime", completed_day="2026-09-28", verified_day="2026-09-28")
        for signal in payload["signals"].values():
            signal.update(close_confirmed=False, market_phase="盘中", state_anchor_day="2026-09-28")
    with pytest.raises(ValueError):
        digest.validate_success_payload(payload)


@pytest.mark.parametrize("product,field,value", [
    ("IC", "total_units_current", -1.),
    ("IM", "core_units_target", -.5),
    ("IM", "total_units_target", 99.),
    ("IC", "total_units_change", 99.),
    ("IM", "momentum_units_change", 99.),
    ("IC", "put_target_core_qty", -1.),
    ("IC", "put_current_core_qty", float("nan")),
    ("IM", "core_put_target_qty_normalized", True),
    ("IM", "total_put_target_qty_normalized", 999.),
    ("IC", "put_target_grid_qty", 1.),
])
def test_quantity_invariants_are_rechecked_at_mail_boundary(product, field, value):
    payload = valid_payload()
    payload["signals"][product][field] = value
    with pytest.raises(ValueError):
        digest.validate_success_payload(payload)


@pytest.mark.parametrize("case", ["orphan_plan", "negative_plan_qty", "missing_positive_contract", "profit_conflict", "monthly_conflict", "profit_missing_contract", "profit_negative_qty", "profit_holiday_execution", "profit_future_trigger"])
def test_pending_plan_invariants_are_rechecked_at_mail_boundary(case):
    payload = valid_payload()
    signal = payload["signals"]["IM"]
    if case in {"orphan_plan", "negative_plan_qty", "missing_positive_contract", "profit_conflict", "monthly_conflict"}:
        ordinary_plan(payload)
        plan = signal["v14_ordinary_put_pending"]
        if case == "orphan_plan":
            signal["v14_ordinary_put_plan_status"] = "not_required"
        elif case == "negative_plan_qty":
            signal["core_put_target_qty_normalized"] = -1.5
            plan["legs"]["core"]["new_qty"] = -1.5
        elif case == "missing_positive_contract":
            signal["core_put_target_contract"] = None
            plan["legs"]["core"]["new_contract"] = None
        elif case == "monthly_conflict":
            signal["option_monthly_reset_due"] = True
        else:
            signal["v14_profit_pending"] = True
            signal["v14_profit_trigger_day"] = "2026-09-30"
    else:
        signal.update(v14_profit_pending=True, v14_profit_trigger_day="2026-09-30",
                      v14_profit_execution_day="2026-10-08",
                      v14_profit_old_contract="MO2612-P-7600", v14_profit_old_qty=1.5,
                      v14_profit_reentry_contract="MO2703-P-7600", v14_profit_reentry_qty=1.5)
        if case == "profit_missing_contract":
            signal["v14_profit_reentry_contract"] = None
        elif case == "profit_negative_qty":
            signal["v14_profit_old_qty"] = -1.5
        elif case == "profit_holiday_execution":
            signal["v14_profit_execution_day"] = "2026-10-01"
        else:
            signal["v14_profit_trigger_day"] = "2026-10-08"
            signal["v14_profit_execution_day"] = "2026-10-09"
    with pytest.raises(ValueError):
        digest.validate_success_payload(payload)


def test_existing_identity_check_control_still_rejects_wrong_rule():
    payload = deepcopy(valid_payload())
    payload["signals"]["IM"]["v14_rule_revision"] = "wrong-rule"
    with pytest.raises(ValueError):
        digest.validate_success_payload(payload)
