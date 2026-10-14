"""Offline counterexamples for the real IC/IM mail-packaging boundary.

The fixture is explicitly synthetic and copies the observed 2026-09-30
producer schema.  It is not a market observation or a trading target.
"""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_ic_im_v1_4_digest as digest
import check_ic_im_v1_4_delivery as gate
import ic_im_v1_4_delivery_contract as delivery_contract


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


def close_fallback_payload():
    value = valid_fix11_payload()
    value.update(market_date="2026-10-14", completed_day="2026-10-14", verified_day="2026-10-14",
                 next_trade_day="2026-10-15", signal_timing="retrospective_replay",
                 generated_at="2026-10-15T01:10:00+08:00", report_generated_at="2026-10-15T01:30:00+08:00")
    for signal in value["signals"].values():
        signal.update(market_date="2026-10-14", next_trade_date="2026-10-15",
            fear_data_status="close_previous_session", fear_greed_index=39.32, fear_data_date="2026-10-13",
            fear_previous_exchange_date="2026-10-13", fear_latest_available_date="2026-10-13",
            fear_page_data_date="2026-10-13", fear_source_url="https://baifenwei.com/data/fear-greed/fear-greed-full.csv",
            fear_csv_sha256="b"*64, fear_close_policy_revision="ic_im_close_fear_previous_session_20261015_v1",
            fear_fallback_applied=True, fear_fallback_reason="same_day_not_published",
            fear_fetch_started_at="2026-10-15T01:19:00+08:00",
            fear_retrieved_at="2026-10-15T01:20:00+08:00", fear_page_updated_at="2026-10-14T21:08:00+08:00",
            fear_fallback_timing="retrospective_replay", signal_generation_timing="retrospective_replay",
            signal_generated_at="2026-10-15T01:21:00+08:00")
    return value


def test_approved_close_fallback_passes_and_is_labeled_in_mail_subject_and_body():
    value = close_fallback_payload()
    digest.validate_success_payload(value)
    subject, body, _ = digest.build_success(value, "", "")
    html = digest.build_success_html(value, "")
    assert "历史模型补录" in subject and "[收盘确认]" not in subject
    assert "retrospective_replay" in body and "沿用上一交易日" in body
    assert "数据日 2026-10-13" in body
    assert "历史模型补录" in html and "不是原信号日已发布" in html


@pytest.mark.parametrize("field,changed", [
    ("fear_data_date", "2026-10-14"), ("fear_data_date", "2026-10-12"),
    ("fear_previous_exchange_date", "2026-10-12"), ("fear_page_data_date", "2026-10-14"),
    ("fear_latest_available_date", "2026-10-12"), ("fear_close_policy_revision", "old"),
    ("fear_fallback_applied", False), ("fear_fallback_reason", "old_cache"),
    ("fear_fallback_timing", "same_day_post_close"), ("close_confirmed", False),
    ("signal_generation_timing", "close_confirmed"), ("signal_generated_at", "2026-10-14T20:00:00+08:00"),
    ("fear_csv_sha256", "bad"), ("fear_source_url", "https://example.invalid/fear.csv"),
    ("fear_page_updated_at", "2026-10-13T15:00:00+08:00"),
    ("fear_page_updated_at", "2026-10-15T02:00:00+08:00"),
    ("fear_retrieved_at", "2026-10-14T14:59:00+08:00"),
    ("fear_retrieved_at", "2026-10-15T01:20:00"), ("fear_greed_index", float("nan")),
])
def test_close_fallback_cannot_bypass_independent_mail_contract(field, changed):
    value = close_fallback_payload()
    for signal in value["signals"].values():
        signal[field] = changed
    with pytest.raises(ValueError):
        digest.validate_success_payload(value)


def test_close_fallback_replay_cannot_be_hidden_by_outer_payload():
    value = close_fallback_payload()
    value.pop("signal_timing")
    with pytest.raises(ValueError, match="historical model replay"):
        digest.validate_success_payload(value)


def test_close_fallback_evidence_must_agree_between_products():
    value = close_fallback_payload()
    value["signals"]["IM"]["fear_close_policy_revision"] = "different"
    with pytest.raises(ValueError, match="snapshots disagree"):
        digest.validate_success_payload(value)


def test_intraday_fallback_is_accepted_only_in_realtime_channel():
    value = close_fallback_payload()
    value.update(publication_mode="realtime", completed_day="2026-10-13", verified_day="2026-10-13",
                 signal_timing="intraday_hypothetical_if_now_close")
    for signal in value["signals"].values():
        signal.update(close_confirmed=False, market_phase="盘中", state_anchor_day="2026-10-13",
                      fear_data_status="intraday_previous_session",
                      fear_intraday_policy_revision="ic_im_intraday_fear_previous_session_20261014_v1",
                      fear_retrieved_at="2026-10-14T14:30:00+08:00",
                      fear_fetch_started_at="2026-10-14T14:29:00+08:00",
                      signal_generated_at="2026-10-14T14:31:00+08:00",
                      signal_generation_timing="intraday_hypothetical_if_now_close",
                      fear_page_updated_at="2026-10-14T06:17:00+08:00")
        signal.pop("fear_fallback_timing", None)
    digest.validate_success_payload(value)


def test_provisional_T_point_can_carry_previous_close_but_must_retain_actual_source_day():
    value = close_fallback_payload()
    for signal in value["signals"].values():
        signal.update(fear_fallback_reason="same_day_not_close_confirmed",
                      fear_latest_available_date="2026-10-14", fear_page_data_date="2026-10-14",
                      fear_page_updated_at="2026-10-14T14:30:00+08:00")
    digest.validate_success_payload(value)
    for signal in value["signals"].values():
        signal["fear_page_updated_at"] = "2026-10-14T15:01:00+08:00"
    with pytest.raises(ValueError, match="qualified T close"):
        digest.validate_success_payload(value)


def test_replayed_ordinary_put_plan_shows_actual_creation_instead_of_original_close_preselection():
    value = close_fallback_payload()["signals"]["IM"]
    value.update(v14_ordinary_put_plan_status="scheduled_t_plus_1_open",
                 v14_ordinary_put_pending={"signal_day":"2026-10-14","execution_day":"2026-10-15",
                     "created_at":"2026-10-15T01:30:00+08:00","creation_timing":"retrospective_replay","legs":{}})
    text = digest.v14_route_reason(value, "IM")
    assert "实际创建2026-10-15T01:30:00+08:00" in text
    assert "2026-10-14收盘预选" not in text


@pytest.mark.parametrize("created", [None, "2026-10-14T20:00:00+08:00", "2026-10-15T01:20:30", "2026-10-15T02:00:00+08:00"])
def test_replayed_ordinary_plan_cannot_erase_or_forge_actual_creation_time(created):
    value=close_fallback_payload()
    signal=ordinary_plan(value)
    signal["v14_ordinary_put_pending"].update(creation_timing="retrospective_replay", research_scope="retrospective_model_plan", created_at=created)
    with pytest.raises(ValueError):
        digest.validate_success_payload(value)


def test_replayed_ordinary_plan_with_bound_actual_creation_passes():
    value=close_fallback_payload()
    signal=ordinary_plan(value)
    signal["v14_ordinary_put_pending"].update(creation_timing="retrospective_replay", research_scope="retrospective_model_plan", created_at="2026-10-15T01:20:30+08:00")
    digest.validate_success_payload(value)


@pytest.fixture
def trusted_synthetic_validation_clock(monkeypatch):
    """Explicit synthetic clock; fixture timestamps are never current observations."""
    clock = datetime.fromisoformat("2026-10-15T02:00:00+08:00")
    monkeypatch.setattr(delivery_contract, "_validation_clock", lambda: clock)
    return clock


def test_close_fallback_is_accepted_under_independent_synthetic_clock(trusted_synthetic_validation_clock):
    value = close_fallback_payload()
    digest.validate_success_payload(value)
    assert datetime.fromisoformat(value["report_generated_at"]) < trusted_synthetic_validation_clock


@pytest.mark.parametrize("fault", [
    "future_retrieved", "future_generated", "generation_before_retrieval", "missing_started",
    "started_after_retrieved", "naive_started", "future_started",
])
def test_independent_fear_clock_rejects_future_inverted_or_missing_evidence(trusted_synthetic_validation_clock, fault):
    value = close_fallback_payload()
    signal = value["signals"]["IC"]
    if fault == "future_retrieved":
        signal.update(fear_fetch_started_at="2026-10-15T02:01:00+08:00",
                      fear_retrieved_at="2026-10-15T02:02:00+08:00",
                      signal_generated_at="2026-10-15T02:03:00+08:00")
    elif fault == "future_generated":
        signal["signal_generated_at"] = "2026-10-15T02:01:00+08:00"
    elif fault == "generation_before_retrieval":
        signal["signal_generated_at"] = "2026-10-15T01:19:59+08:00"
    elif fault == "missing_started":
        signal.pop("fear_fetch_started_at")
    elif fault == "started_after_retrieved":
        signal["fear_fetch_started_at"] = "2026-10-15T01:20:01+08:00"
    elif fault == "naive_started":
        signal["fear_fetch_started_at"] = "2026-10-15T01:19:00"
    elif fault == "future_started":
        signal["fear_fetch_started_at"] = "2026-10-16T01:19:00+08:00"
    with pytest.raises(ValueError):
        delivery_contract.validate_fear_clock(signal, datetime.fromisoformat("2026-10-14").date(), "close_confirmed")


def test_complete_consumer_rejects_self_consistent_future_collection_and_report(trusted_synthetic_validation_clock):
    value = close_fallback_payload()
    value["report_generated_at"] = "2026-10-16T01:30:00+08:00"
    for signal in value["signals"].values():
        signal.update(fear_fetch_started_at="2026-10-16T01:19:00+08:00",
                      fear_retrieved_at="2026-10-16T01:20:00+08:00",
                      signal_generated_at="2026-10-16T01:21:00+08:00")
    with pytest.raises(ValueError, match="independent validation clock"):
        digest.validate_success_payload(value)


@pytest.mark.parametrize("fault", ["missing", "naive", "future", "before_both_legs", "before_im_leg_only"])
def test_report_completion_cannot_precede_any_leg_or_exceed_independent_clock(trusted_synthetic_validation_clock, fault):
    value = close_fallback_payload()
    if fault == "missing":
        value.pop("report_generated_at")
    elif fault == "naive":
        value["report_generated_at"] = "2026-10-15T01:30:00"
    elif fault == "future":
        value["report_generated_at"] = "2026-10-15T02:00:01+08:00"
    elif fault == "before_both_legs":
        value["report_generated_at"] = "2026-10-15T01:20:30+08:00"
    else:
        value["signals"]["IM"]["signal_generated_at"] = "2026-10-15T01:22:00+08:00"
        value["report_generated_at"] = "2026-10-15T01:21:30+08:00"
    with pytest.raises(ValueError):
        digest.validate_success_payload(value)


@pytest.mark.parametrize("field,changed", [
    ("fear_fallback_applied", True), ("fear_fallback_applied", "false"),
    ("fear_fallback_reason", "same_day_not_published"),
    ("fear_fallback_timing", "retrospective_replay"),
])
def test_nonfallback_status_cannot_carry_contradictory_metadata(trusted_synthetic_validation_clock, field, changed):
    value = close_fallback_payload()
    for signal in value["signals"].values():
        signal.update(fear_data_status="retrospective_replay", fear_data_date="2026-10-14",
                      fear_latest_available_date="2026-10-14", fear_page_data_date="2026-10-14",
                      fear_fallback_applied=False, fear_fallback_reason=None, fear_fallback_timing=None)
    digest.validate_success_payload(value)
    for signal in value["signals"].values():
        signal[field] = changed
    with pytest.raises(ValueError, match="contradictory|boolean"):
        digest.validate_success_payload(value)


@pytest.mark.parametrize("page_update", [
    "2026-10-16T02:00:00+08:00",  # Future relative to the independent clock.
    "2026-10-15T01:21:00+08:00",  # Before validation, but after completed retrieval.
    "2026-10-15T01:00:00",        # Missing timezone cannot prove snapshot ordering.
])
def test_nonfallback_page_timestamp_must_be_bound_to_its_acquisition(trusted_synthetic_validation_clock, page_update):
    value = close_fallback_payload()
    for signal in value["signals"].values():
        signal.update(fear_data_status="retrospective_replay", fear_data_date="2026-10-14",
                      fear_latest_available_date="2026-10-14", fear_page_data_date="2026-10-14",
                      fear_fallback_applied=False, fear_fallback_reason=None, fear_fallback_timing=None)
    digest.validate_success_payload(value)
    for signal in value["signals"].values():
        signal["fear_page_updated_at"] = page_update
    with pytest.raises(ValueError):
        digest.validate_success_payload(value)


@pytest.mark.parametrize("erased", ["outer", "leg", "both"])
def test_real_collection_dates_prevent_erasing_replay_labels(trusted_synthetic_validation_clock, erased):
    value = close_fallback_payload()
    if erased in {"outer", "both"}:
        value.pop("signal_timing")
    if erased in {"leg", "both"}:
        for signal in value["signals"].values():
            signal.pop("signal_generation_timing")
    with pytest.raises(ValueError):
        digest.validate_success_payload(value)


def test_pending_plan_replay_is_derived_from_actual_dates_after_all_labels_are_erased(trusted_synthetic_validation_clock):
    value = close_fallback_payload()
    signal = ordinary_plan(value)
    signal["v14_ordinary_put_pending"].update(creation_timing="retrospective_replay",
        research_scope="retrospective_model_plan", created_at="2026-10-15T01:20:30+08:00")
    signal.pop("signal_generation_timing")
    signal.pop("fear_fallback_timing")
    signal["fear_data_status"] = "same_day_post_close"
    with pytest.raises(ValueError, match="erase real replay identity"):
        delivery_contract.validate_pending_plans("IM", signal)


@pytest.mark.parametrize("generated", ["2026-10-15T07:00:00+08:00", "2026-10-14T23:00:00Z"])
def test_next_bj_day_generation_cannot_hide_replay_with_utc_spelling(monkeypatch, generated):
    """Same synthetic instant, with T acquisition and T+1 Beijing generation."""
    checked = datetime.fromisoformat("2026-10-15T08:00:00+08:00")
    monkeypatch.setattr(delivery_contract, "_validation_clock", lambda: checked)
    assert datetime.fromisoformat(generated) == datetime.fromisoformat("2026-10-15T07:00:00+08:00")
    value = close_fallback_payload()
    value.pop("signal_timing")
    value["report_generated_at"] = "2026-10-15T07:01:00+08:00"
    for signal in value["signals"].values():
        signal.update(fear_fetch_started_at="2026-10-14T19:59:00+08:00",
                      fear_retrieved_at="2026-10-14T20:00:00+08:00",
                      fear_page_updated_at="2026-10-14T19:00:00+08:00",
                      fear_fallback_timing="same_day_post_close", signal_generated_at=generated)
        signal.pop("signal_generation_timing")
    im = ordinary_plan(value)
    for field in ("creation_timing", "research_scope", "created_at"):
        im["v14_ordinary_put_pending"].pop(field, None)
    with pytest.raises(ValueError, match="replay|retrospective"):
        delivery_contract.validate_pending_plans("IM", im)
    with pytest.raises(ValueError, match="replay|retrospective"):
        digest.validate_success_payload(value)
