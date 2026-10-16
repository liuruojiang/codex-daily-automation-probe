"""Forward synthetic boundary probes plus an unchanged saved real archive."""
from copy import deepcopy
from datetime import datetime
import importlib.util
import hashlib
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_ic_im_v1_4_digest as digest
import ic_im_put_execution_contract as execution
import ic_im_v1_4_delivery_contract as contract


def validate(product, signal):
    execution.validate_execution(product, signal, is_session=contract.is_session,
                                 adjacent_session=contract.adjacent_session)


def identity(product, side="new"):
    return dict(contract=("510500P2612M06000" if side == "new" else "510500P2611M06000")
                if product == "IC" else ("MO2612-P-6200" if side == "new" else "MO2611-P-6200"),
                security_id=("10013000" if side == "new" else "10012000") if product == "IC" else None,
                kind="option")


def plan(product="IC", kind="short_put", action="ENTER_SHORT_PUT", signal_day="2026-10-16", execution_day="2026-10-19"):
    new, old = identity(product), identity(product, "old")
    qty = 5 if product == "IC" else 1.5
    value = dict(product=product, signal_day=signal_day, execution_day=execution_day,
                 new_contract=new["contract"], new_security_id=new["security_id"], new_qty=qty,
                 required_quotes=[new])
    if kind == "profit3x":
        value.update(old_contract=old["contract"], old_security_id=old["security_id"], old_qty=qty)
        value["required_quotes"].append(old)
    else:
        previous = dict(v14_route_state="future" if action == "ENTER_SHORT_PUT" else "short_put",
                        v14_short_put_open_pending=None, post_core_contract=product+"2612",
                        v14_core_put_qty=qty, verified_core_put_qty_normalized=qty,
                        v14_core_put_contract=old["contract"], v14_core_put_security_id=old["security_id"],
                        post_core_put_contract=old["contract"],
                        v14_short_put_contract=old["contract"], v14_short_put_security_id=old["security_id"],
                        v14_short_put_qty_normalized=qty, v14_short_put_entry_premium=80.)
        value.update(previous_anchor=previous, action=action, signal_premium=100., execution_revision=execution.REVISION)
        value["required_quotes"] += ([dict(contract=product+"2612", security_id=None, kind="future"), old]
                                     if action == "ENTER_SHORT_PUT" else [old])
    return value


def signal(product="IC", pending=True):
    value = dict(product=product, market_date="2026-10-16", v14_execution_revision=execution.REVISION,
                 v14_opening_events=[], v14_short_put_open_pending=None,
                 v14_short_put_entry_premium=None, v14_short_put_entry_price_day=None,
                 v14_short_put_cost_basis=None, v14_route_state="future")
    if pending:
        new = plan(product)
        value.update(v14_route_state="short_put", v14_short_put_open_pending=new,
                     v14_short_put_contract=new["new_contract"], v14_short_put_security_id=new["new_security_id"],
                     v14_short_put_qty_normalized=new["new_qty"], v14_short_put_cost_basis="pending_dated_open")
    return value


def event(product="IC", kind="profit3x"):
    value = plan(product, kind, signal_day="2026-10-15", execution_day="2026-10-16")
    return dict(kind=kind, plan=value, status="confirmed_open_research_price",
                prices=[dict(row, openprice=120., price_day="2026-10-16", source="synthetic_boundary_fixture")
                        for row in value["required_quotes"]])


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_pending_none_cost_is_accepted_and_prominent(product):
    item = signal(product)
    validate(product, item)
    assert execution.needs_attention(item)
    text = "".join(execution.display_lines(product, item))
    assert "待核验" in text and "不是入场成本" in text and "不能启用" in text


@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("kind", ["profit3x", "short_put"])
def test_all_opening_legs_required_before_confirmation(product, kind):
    item = signal(product, False)
    item["v14_opening_events"] = [event(product, kind)]
    validate(product, item)
    assert "指定日开盘证据" in "".join(execution.display_lines(product, item))
    item["v14_opening_events"][0]["prices"].pop()
    with pytest.raises(ValueError):
        validate(product, item)


@pytest.mark.parametrize("mutation", ["missing_field", "bad_revision", "bad_object", "wrong_product", "wrong_date",
                                       "weekend", "wrong_contract", "wrong_security", "extra_quote", "candidate_cost",
                                       "missing_plan", "wrong_qty", "nested_previous", "wrong_previous_route"])
def test_pending_corruption_is_rejected(mutation):
    item = signal()
    pending = item["v14_short_put_open_pending"]
    if mutation == "missing_field": del item["v14_opening_events"]
    elif mutation == "bad_revision": item["v14_execution_revision"] = "unknown"
    elif mutation == "bad_object": item["v14_short_put_open_pending"] = {"malformed": True}
    elif mutation == "wrong_product": pending["product"] = "IM"
    elif mutation == "wrong_date": pending["execution_day"] = "2026-10-20"
    elif mutation == "weekend": pending["execution_day"] = "2026-10-17"
    elif mutation == "wrong_contract": pending["new_contract"] = "MO2612-P-6200"
    elif mutation == "wrong_security": pending["new_security_id"] = "another-security"
    elif mutation == "extra_quote": pending["required_quotes"].append(identity("IC"))
    elif mutation == "candidate_cost": item["v14_short_put_entry_premium"] = 100.
    elif mutation == "missing_plan": item["v14_short_put_open_pending"] = None
    elif mutation == "wrong_qty": item["v14_short_put_qty_normalized"] = 4
    elif mutation == "nested_previous": pending["previous_anchor"]["v14_short_put_open_pending"] = {}
    elif mutation == "wrong_previous_route": pending["previous_anchor"]["v14_route_state"] = "cash_wait"
    with pytest.raises(ValueError): validate("IC", item)


@pytest.mark.parametrize("mutation", ["duplicate", "nan", "bool", "wrong_date", "no_source", "wrong_identity", "unknown_status", "wrong_quantity"])
def test_confirmed_opening_corruption_is_rejected(mutation):
    item = signal("IM", False)
    evidence = event("IM")
    item["v14_opening_events"] = [evidence]
    if mutation == "duplicate": evidence["prices"].append(deepcopy(evidence["prices"][0]))
    elif mutation == "nan": evidence["prices"][0]["openprice"] = float("nan")
    elif mutation == "bool": evidence["prices"][0]["openprice"] = True
    elif mutation == "wrong_date": evidence["prices"][0]["price_day"] = "2026-10-15"
    elif mutation == "no_source": evidence["prices"][0]["source"] = ""
    elif mutation == "wrong_identity": evidence["prices"][0]["contract"] = "MO2612-P-6000"
    elif mutation == "unknown_status": evidence["status"] = "filled"
    elif mutation == "wrong_quantity": evidence["plan"]["new_qty"] = -1
    with pytest.raises(ValueError): validate("IM", item)


def test_duplicate_event_and_false_confirmed_cost_rejected():
    item = signal("IM", False)
    item["v14_opening_events"] = [event("IM"), event("IM")]
    with pytest.raises(ValueError): validate("IM", item)
    item["v14_opening_events"] = []
    new = identity("IM")
    item.update(v14_route_state="short_put", v14_short_put_contract=new["contract"],
                v14_short_put_qty_normalized=1.5,
                v14_short_put_security_id=None, v14_short_put_entry_price_day="2026-10-16",
                v14_short_put_entry_premium=120., v14_short_put_cost_basis="confirmed_dated_open")
    with pytest.raises(ValueError): validate("IM", item)
    item["v14_opening_events"] = [event("IM", "short_put")]
    validate("IM", item)
    item["v14_short_put_entry_premium"] = 121.
    with pytest.raises(ValueError): validate("IM", item)


def test_calendar_only_failure_never_uses_stale_targets(monkeypatch):
    monkeypatch.setattr(contract, "_validation_clock", lambda: datetime(2026, 10, 16, 14, 20, tzinfo=ZoneInfo("Asia/Shanghai")))
    payload = dict(status="failed", publication_mode="realtime", market_date="2026-10-16",
                   report_generated_at="2026-10-16T14:15:00+08:00",
                   signals={"IC": {"put_target_contract": "SECRET-OLD-TARGET", "market_date": "2026-09-18"}})
    for text in (digest.build_failure(payload, "", "")[1], digest.build_failure_html(payload, "")):
        assert text.count("月度Put展期换仓提醒") == 2
        assert "今日（2026-10-16）" in text and "尚未确认" in text
        assert "SECRET-OLD-TARGET" not in text
    payload["report_generated_at"] = "2026-10-15T14:15:00+08:00"
    assert not digest.partial_calendar_notices(payload)


@pytest.mark.parametrize("mutation", ["naive", "future", "after_close_realtime", "pre_close_confirmed", "stale_day", "replay", "unknown_status"])
def test_failed_calendar_clock_and_mode_boundaries(monkeypatch, mutation):
    monkeypatch.setattr(contract, "_validation_clock", lambda: datetime(2026, 10, 16, 20, 20, tzinfo=ZoneInfo("Asia/Shanghai")))
    item = dict(status="failed", publication_mode="realtime", market_date="2026-10-16",
                report_generated_at="2026-10-16T14:15:00+08:00")
    if mutation == "naive": item["report_generated_at"] = "2026-10-16T14:15:00"
    elif mutation == "future": item["report_generated_at"] = "2026-10-16T20:25:00+08:00"
    elif mutation == "after_close_realtime": item["report_generated_at"] = "2026-10-16T15:00:00+08:00"
    elif mutation == "pre_close_confirmed": item["publication_mode"] = "close_confirmed"
    elif mutation == "stale_day": item["market_date"] = "2026-10-15"
    elif mutation == "replay": item["signal_timing"] = "retrospective_replay"
    elif mutation == "unknown_status": item["status"] = "unknown"
    assert not digest.partial_calendar_notices(item)


def test_top_level_replay_and_independent_calendar_block_false_reminders():
    from test_ic_im_v1_4_monthly_maintenance_notice import payload
    item = payload()
    item["signal_timing"] = "retrospective_replay"
    for value in item["signals"].values(): value.pop("signal_generation_timing", None)
    assert all("非原日已发布提醒" in text and "明日（" not in text for text in digest.calendar_notices(item))
    for value in item["signals"].values():
        value.update(option_monthly_reset_execution_date="2026-09-21", put_monthly_reset_execution_date="2026-09-21", next_trade_date="2026-09-21")
    assert not digest.calendar_notices(item)


def test_saved_real_archive_remains_compatible():
    saved = json.loads((ROOT / "tests/fixtures/icim/readiness_20261009/result.json").read_text(encoding="utf-8"))
    before = deepcopy(saved)
    subject, text, _ = digest.build_success(saved, "", "")
    assert subject and text and digest.build_success_html(saved, "")
    # Hashes read from exact pre-change GitHub main renderer 5520fce...
    assert hashlib.sha256(subject.encode()).hexdigest() == "83ac10169794288c490b74269a01f7f2bd4411b288b5714835430fece77b9e4e"
    assert hashlib.sha256(text.encode()).hexdigest() == "91ad9a1f7b782eba5afab05150641a997f7ffceb504ec9d29414d7b7aab263d7"
    assert hashlib.sha256(digest.build_success_html(saved, "").encode()).hexdigest() == "84feb7e87780d5a2568d7e13121f4501119018df8b19b41a54886fefe1e4fc73"
    assert saved == before
    for product, item in saved["signals"].items(): validate(product, item)


def test_frozen_old_new_profit_quantities_need_not_be_equal():
    item = signal("IM", False)
    evidence = event("IM")
    evidence["plan"]["new_qty"] = 3.
    item["v14_opening_events"] = [evidence]
    validate("IM", item)


def test_legacy_short_entry_cost_does_not_activate_new_contract():
    validate("IM", dict(market_date="2026-10-15", v14_route_state="short_put", v14_short_put_entry_premium=100.))


def forward_payload():
    """Synthetic October 16 boundary payload; not an observed market signal."""
    value = json.loads((ROOT / "tests/fixtures/icim/readiness_20261009/result.json").read_text(encoding="utf-8"))
    value.update(market_date="2026-10-16", completed_day="2026-10-16", verified_day="2026-10-16",
                 next_trade_day="2026-10-19", generated_at="2026-10-16T20:05:00+08:00",
                 report_generated_at="2026-10-16T20:05:00+08:00", signal_timing="close_confirmed")
    for product, item in value["signals"].items():
        item.update(signal(product, False))
        item.update(next_trade_date="2026-10-19", close_confirmed=True,
                    fear_greed_index=35., fear_data_date="2026-10-16", fear_data_status="same_day_post_close",
                    fear_retrieved_at="2026-10-16T20:00:00+08:00", fear_page_updated_at="2026-10-16T19:59:00+08:00",
                    signal_generated_at="2026-10-16T20:01:00+08:00", signal_generation_timing="close_confirmed",
                    fear_fallback_applied=False, fear_fallback_reason=None, fear_fallback_timing=None,
                    option_monthly_reset_due=False, option_monthly_reset_preview=False, put_monthly_reset_preview=False,
                    v14_ordinary_put_pending=None, v14_ordinary_put_plan_status="not_required")
    return value


def test_forward_complete_payload_reaches_subject_plain_html_without_relaxing_gates(monkeypatch):
    monkeypatch.setattr(contract, "_validation_clock", lambda: datetime(2026, 10, 16, 20, 10, tzinfo=ZoneInfo("Asia/Shanghai")))
    payload = forward_payload()
    payload["signals"]["IC"].update(signal("IC"))
    subject, text, _ = digest.build_success(payload, "", "")
    html = digest.build_success_html(payload, "")
    assert "Put开盘待核验" in subject
    for rendered in (text, html):
        assert "卖Put开盘计划待核验" in rendered and "不是入场成本" in rendered
    payload["signals"]["IC"]["v14_short_put_open_pending"] = {"malformed": True}
    with pytest.raises(ValueError): digest.build_success(payload, "", "")
