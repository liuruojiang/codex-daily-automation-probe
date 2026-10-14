"""Independent offline date and quantity checks for modern IC/IM delivery.

The calendar below is the published common CFFEX/SSE 2026 calendar already
used by the official strategy.  Its source is
liuruojiang/ic-im-rolling-arbitrage@739071a201506dd869b9a156e490c3bafd95a334,
poe_ic_im_mainline_v1_4_bot.py::_EXCHANGE_CLOSURES and
_OFFICIAL_EXCHANGE_CALENDAR_YEARS.  This is an explicit covered-year snapshot,
never a NAV-derived or weekday-only calendar.  New years fail closed until
their official schedule is added and checked against the strategy calendar.
"""
from datetime import date, datetime, time, timedelta
import math
import re
from typing import Any
from zoneinfo import ZoneInfo


CALENDAR_SOURCE_REVISION = "739071a201506dd869b9a156e490c3bafd95a334"
CALENDAR_YEARS = frozenset({2026})
EXCHANGE_CLOSURES = frozenset({
    date(2026, 1, 1), date(2026, 1, 2),
    *[date(2026, 2, day) for day in range(16, 24)],
    date(2026, 4, 6), date(2026, 5, 1), date(2026, 5, 4), date(2026, 5, 5),
    date(2026, 6, 19), date(2026, 9, 25),
    *[date(2026, 10, day) for day in range(1, 8)],
})


def is_session(day: date) -> bool:
    if day.year not in CALENDAR_YEARS:
        raise ValueError(f"independent exchange calendar does not cover {day.year}")
    return day.weekday() < 5 and day not in EXCHANGE_CLOSURES


def adjacent_session(day: date, direction: int = 1) -> date:
    if direction not in {-1, 1}:
        raise ValueError("calendar direction must be -1 or 1")
    candidate = day + timedelta(days=direction)
    while not is_session(candidate):
        candidate += timedelta(days=direction)
    return candidate


def validate_session_dates(days: dict[str, date], publication_mode: str) -> None:
    for field, day in days.items():
        if not is_session(day):
            raise ValueError(f"{field} is not a common exchange trading session")
    if days["next_trade_day"] != adjacent_session(days["market_date"]):
        raise ValueError("next_trade_day is not the next actual common exchange session")
    if publication_mode == "realtime" and days["completed_day"] != adjacent_session(days["market_date"], -1):
        raise ValueError("realtime completed day is not the previous actual common exchange session")


def _validation_clock() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def validate_fear_clock(signal: dict[str, Any], day: date, mode: str) -> None:
    if day < date(2026, 10, 14):
        return
    carried = signal.get("fear_data_status") in {"close_previous_session", "intraday_previous_session"}
    applied = signal.get("fear_fallback_applied")
    if applied is not None and type(applied) is not bool:
        raise ValueError("Fear fallback flag must be boolean")
    if not carried and (applied is True or signal.get("fear_fallback_reason") is not None
                        or signal.get("fear_fallback_timing") is not None):
        raise ValueError("nonfallback Fear status has contradictory carry evidence")
    beijing = ZoneInfo("Asia/Shanghai")
    fields = ["fear_retrieved_at", "fear_page_updated_at"]
    if carried or signal.get("fear_fetch_started_at") is not None:
        fields.append("fear_fetch_started_at")
    if signal.get("signal_generated_at") is not None:
        fields.append("signal_generated_at")
    stamps = {}
    for field in fields:
        stamp = datetime.fromisoformat(str(signal.get(field)))
        if stamp.tzinfo is None:
            raise ValueError("Fear collection/generation clock lacks timezone")
        stamps[field] = stamp.astimezone(beijing)
    retrieved = stamps["fear_retrieved_at"]
    if stamps["fear_page_updated_at"] > retrieved:
        raise ValueError("Fear page update is later than completed retrieval")
    checked = _validation_clock()
    if checked.tzinfo is None or retrieved > checked.astimezone(beijing):
        raise ValueError("Fear retrieval is later than independent validation clock")
    if "fear_fetch_started_at" in stamps and stamps["fear_fetch_started_at"] > retrieved:
        raise ValueError("Fear collection starts after completed retrieval")
    if mode == "realtime" and (retrieved.date() != day or retrieved.time() >= time(15)):
        raise ValueError("Fear realtime retrieval must complete on T before 15:00")
    generated = stamps.get("signal_generated_at")
    if generated is not None and not retrieved <= generated <= checked.astimezone(beijing):
        raise ValueError("Fear generation is outside collection/independent validation interval")
    if (retrieved.date() > day or (generated is not None and generated.date() > day)) and (generated is None
            or signal.get("signal_generation_timing") != "retrospective_replay"):
        raise ValueError("cross-day Fear signal must retain real generation and retrospective identity")


def validate_report_clock(payload: dict[str, Any], day: date) -> None:
    if day < date(2026, 10, 14):
        return
    values = [s.get("signal_generated_at") for s in payload["signals"].values()
              if s.get("signal_generated_at") is not None]
    if not values and payload.get("report_generated_at") is None:
        return  # Read-only sparse archives predate the real completion field.
    completed = datetime.fromisoformat(str(payload.get("report_generated_at")))
    checked = _validation_clock()
    if completed.tzinfo is None or not completed <= checked:
        raise ValueError("report completion is later than independent validation clock or lacks timezone")
    for value in values:
        generated = datetime.fromisoformat(str(value))
        if generated.tzinfo is None or generated > completed:
            raise ValueError("signal generation is later than actual report completion")


def validate_fear_fallback(signal: dict[str, Any], day: date, mode: str) -> None:
    """Independent source/date/time boundary for the explicitly approved carry."""
    close = mode == "close_confirmed"
    expected_status = "close_previous_session" if close else "intraday_previous_session"
    revision_field = "fear_close_policy_revision" if close else "fear_intraday_policy_revision"
    revision = ("ic_im_close_fear_previous_session_20261015_v1" if close else
                "ic_im_intraday_fear_previous_session_20261014_v1")
    if day < date(2026, 10, 14) or not is_session(day):
        raise ValueError("Fear fallback is outside its approved signal-day/calendar scope")
    validate_fear_clock(signal, day, mode)
    prior = adjacent_session(day, -1)
    reason = signal.get("fear_fallback_reason")
    source_day = day if close and reason == "same_day_not_close_confirmed" else prior
    if (signal.get("fear_data_status") != expected_status or signal.get(revision_field) != revision
            or signal.get("fear_fallback_applied") is not True
            or reason not in ({"same_day_not_published", "same_day_not_close_confirmed"} if close else {"same_day_not_published"})
            or signal.get("close_confirmed") is not close
            or any(signal.get(field) != prior.isoformat() for field in (
                "fear_data_date", "fear_previous_exchange_date"))
            or any(signal.get(field) != source_day.isoformat() for field in ("fear_latest_available_date", "fear_page_data_date"))):
        raise ValueError("Fear fallback revision/date/mode evidence invalid")
    value = signal.get("fear_greed_index")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError("Fear fallback score invalid")
    if signal.get("fear_source_url") != "https://baifenwei.com/data/fear-greed/fear-greed-full.csv" or re.fullmatch(
        r"[0-9a-f]{64}", str(signal.get("fear_csv_sha256", ""))
    ) is None:
        raise ValueError("Fear fallback source/hash invalid")
    stamps = {}
    beijing = ZoneInfo("Asia/Shanghai")
    for field in ("fear_retrieved_at", "fear_page_updated_at"):
        stamp = datetime.fromisoformat(str(signal.get(field)))
        if stamp.tzinfo is None:
            raise ValueError("Fear fallback time lacks timezone")
        stamps[field] = stamp.astimezone(beijing)
    retrieved, updated = stamps["fear_retrieved_at"], stamps["fear_page_updated_at"]
    if updated > retrieved or updated <= datetime.combine(prior, time(15), tzinfo=beijing):
        raise ValueError("Fear fallback page lacks prior-session close evidence")
    if close:
        if reason == "same_day_not_close_confirmed" and (updated.date() != day or updated > datetime.combine(day, time(15), tzinfo=beijing)):
            raise ValueError("qualified T close evidence cannot be discarded for carried Fear")
        if retrieved.date() < day or (retrieved.date() == day and retrieved.time() <= time(15)):
            raise ValueError("Fear close fallback was not acquired after signal close")
        timing = "retrospective_replay" if retrieved.date() > day else "same_day_post_close"
        if signal.get("fear_fallback_timing") != timing:
            raise ValueError("Fear fallback replay timing invalid")
        if timing == "retrospective_replay":
            generated = datetime.fromisoformat(str(signal.get("signal_generated_at")))
            if (generated.tzinfo is None or generated.astimezone(beijing) < retrieved
                    or signal.get("signal_generation_timing") != "retrospective_replay"):
                raise ValueError("Fear fallback replay must retain real signal generation time")
    elif retrieved.date() != day or retrieved.time() >= time(15):
        raise ValueError("Fear intraday fallback was not acquired on T before 15:00")


def quantity(value: Any, label: str, *, signed: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} requires a finite numeric quantity")
    if not signed and value < 0:
        raise ValueError(f"{label} quantity must be nonnegative")
    return float(value)


def _same(left: float, right: float, label: str) -> None:
    if not math.isclose(left, right, abs_tol=1e-9, rel_tol=1e-9):
        raise ValueError(f"{label} quantity formula does not match")


def validate_quantities(product: str, signal: dict[str, Any]) -> None:
    """Check recorded quantities; never infer missing optional archive fields.

    Sparse older presentation payloads can omit a complete sleeve table.
    Every present quantity is checked, and every complete table is reconciled.
    Momentum weights have no invented upper cap: 0.5 * weight is the producer
    conversion, including legitimate weights above one.
    """
    signed_fields = {"total_units_change", "momentum_units_change"}
    fields = {
        "core_units_current", "core_units_target", "momentum_units_current",
        "momentum_units_target", "total_units_current", "total_units_target",
        "momentum_current_weight", "momentum_next_weight", "grid_current", "grid_target",
        "v14_short_put_qty_normalized", "v14_core_put_qty", *signed_fields,
        "v14_profit_old_qty", "v14_profit_reentry_qty",
    }
    if product == "IC":
        fields.update(f"put_{when}_{leg}_qty" for when in ("current", "target")
                      for leg in ("core", "momentum", "grid", "total"))
    else:
        fields.update(f"{leg}_put_{when}_qty_normalized" for when in ("current", "target")
                      for leg in ("core", "momentum", "total"))
    # Current fix11 producers always emit the complete table.  Deleting a leg
    # must not turn its sum check into an archive compatibility shortcut.
    if date.fromisoformat(signal["market_date"]) >= date(2026, 10, 8):
        missing = fields - signal.keys()
        if missing:
            raise ValueError(f"{product} current producer lacks required quantity fields: {sorted(missing)}")
    for field in fields:
        if field in signal:
            quantity(signal[field], f"{product} {field}", signed=field in signed_fields)
    for when, weight_field in (("current", "momentum_current_weight"), ("target", "momentum_next_weight")):
        momentum = f"momentum_units_{when}"
        if momentum in signal:
            _same(signal[momentum], .5 * signal[weight_field], f"{product} {momentum}")
        legs = (f"core_units_{when}", momentum, f"grid_{when}")
        if all(field in signal for field in legs):
            _same(signal[f"total_units_{when}"], sum(signal[field] for field in legs), f"{product} futures {when} total")
        if product == "IC":
            put_legs = [f"put_{when}_{leg}_qty" for leg in ("core", "momentum", "grid")]
            total = f"put_{when}_total_qty"
            grid = f"put_{when}_grid_qty"
            if grid in signal and signal[grid] != 0:
                raise ValueError("IC grid has no Put protection quantity")
        else:
            put_legs = [f"{leg}_put_{when}_qty_normalized" for leg in ("core", "momentum")]
            total = f"total_put_{when}_qty_normalized"
        if total in signal and all(field in signal for field in put_legs):
            _same(signal[total], sum(signal[field] for field in put_legs), f"{product} Put {when} total")
    for sleeve in ("total", "momentum"):
        field = f"{sleeve}_units_change"
        if field in signal and all(f"{sleeve}_units_{when}" in signal for when in ("current", "target")):
            _same(signal[field], signal[f"{sleeve}_units_target"] - signal[f"{sleeve}_units_current"], f"{product} {field}")


def _day(value: Any, label: str) -> date:
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError(f"{label} requires ISO date")
    try:
        day = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} requires ISO date") from exc
    if not is_session(day):
        raise ValueError(f"{label} is not an exchange session")
    return day


def validate_pending_plans(product: str, signal: dict[str, Any]) -> None:
    if date.fromisoformat(signal["market_date"]) >= date(2026, 10, 8):
        required = {
            "v14_route_state", "v14_ordinary_put_pending", "v14_profit_pending",
            "v14_roll_pending", "v14_settlement_pending", "v14_profit_trigger_day",
            "v14_profit_execution_day", "v14_profit_old_contract", "v14_profit_reentry_contract",
        }
        if required - signal.keys():
            raise ValueError(f"{product} current producer lacks required pending-plan state")
    plan = signal.get("v14_ordinary_put_pending")
    scheduled = signal.get("v14_ordinary_put_plan_status") == "scheduled_t_plus_1_open"
    if (plan is not None) != scheduled:
        raise ValueError(f"{product} ordinary Put pending/status mismatch")
    for flag in ("v14_profit_pending", "v14_roll_pending", "v14_settlement_pending"):
        if flag in signal and type(signal[flag]) is not bool:
            raise ValueError(f"{product} {flag} must be boolean")
    route = signal.get("v14_route_state", "future")
    if route not in {"future", "short_put", "assigned_etf", "cash_wait", "recovery_future"}:
        raise ValueError(f"{product} core route state is invalid")
    for kind in ("roll", "settlement"):
        if signal.get(f"v14_{kind}_pending"):
            if route != "short_put":
                raise ValueError(f"{product} {kind} pending requires the short-Put route")
            trigger = _day(signal.get(f"v14_{kind}_trigger_day"), f"{product} {kind} trigger")
            if trigger > _day(signal.get("market_date"), f"{product} signal day"):
                raise ValueError(f"{product} {kind} trigger cannot be later than its signal")
    if plan is not None:
        day = date.fromisoformat(signal["market_date"])
        replay = (signal.get("signal_generation_timing") == "retrospective_replay"
                  or signal.get("fear_data_status") == "retrospective_replay"
                  or signal.get("fear_fallback_timing") == "retrospective_replay"
                  or any(datetime.fromisoformat(str(signal[field])).astimezone(ZoneInfo("Asia/Shanghai")).date() > day
                         for field in ("fear_retrieved_at", "signal_generated_at")
                         if signal.get(field) is not None))
        if (day >= date(2026, 10, 14) and replay
                and plan.get("signal_day") == signal.get("market_date")):
            if signal.get("signal_generation_timing") != "retrospective_replay":
                raise ValueError("replayed ordinary Put plan cannot erase real replay identity")
            if plan.get("creation_timing") != "retrospective_replay" or plan.get("research_scope") != "retrospective_model_plan":
                raise ValueError("replayed ordinary Put plan lacks creation identity")
            stamps = [datetime.fromisoformat(str(value)) for value in (
                signal.get("fear_retrieved_at"), plan.get("created_at"), signal.get("signal_generated_at"))]
            if (any(stamp.tzinfo is None for stamp in stamps)
                    or not stamps[0] <= stamps[1] <= stamps[2] <= _validation_clock()):
                raise ValueError("replayed ordinary Put plan creation is outside actual collection interval")
        if signal.get("option_monthly_reset_due"):
            raise ValueError(f"{product} ordinary Put plan conflicts with monthly maintenance")
        if (route != "future" or signal.get("v14_profit_pending")
                or signal.get("v14_action") in {"CORE_PUT_PROFIT3X_REENTER", "EXECUTE_CORE_PUT_PROFIT3X_REENTER"}):
            legs = plan.get("legs") if isinstance(plan, dict) else None
            core = legs.get("core") if isinstance(legs, dict) else None
            if not isinstance(core, dict) or core.get("changed") is not False:
                raise ValueError(f"{product} ordinary Put plan cannot change a route/3x-owned core")
            # The digest's per-leg gate binds both unchanged core identities,
            # security IDs and quantities to the appropriate held stage.
    if signal.get("v14_profit_pending"):
        if route != "future":
            raise ValueError(f"{product} 3x plan requires the future core route")
        maintenance_premium = signal.get("v14_core_put_target_entry_premium")
        # The producer retains an auditable unresolved plan if maintenance
        # has no price.  A completed priced monthly reset supersedes it.
        if (signal.get("option_monthly_reset_due") and signal.get("close_confirmed") is True
                and isinstance(maintenance_premium, (int, float))
                and not isinstance(maintenance_premium, bool)
                and math.isfinite(maintenance_premium) and maintenance_premium > 0):
            raise ValueError(f"{product} priced monthly maintenance supersedes the 3x pending plan")
        trigger = _day(signal.get("v14_profit_trigger_day"), f"{product} 3x trigger")
        execution = _day(signal.get("v14_profit_execution_day"), f"{product} 3x execution")
        if trigger > _day(signal.get("market_date"), f"{product} signal day"):
            raise ValueError(f"{product} 3x trigger cannot be later than its signal")
        if execution != adjacent_session(trigger):
            raise ValueError(f"{product} 3x execution must be the next common session")
        if trigger >= date(2026, 9, 28):
            for side in ("old", "reentry"):
                field = f"v14_profit_{side}_qty"
                if quantity(signal.get(field), f"{product} {field}") <= 0:
                    raise ValueError(f"{product} 3x quantity must be positive")
                if not signal.get(f"v14_profit_{side}_contract"):
                    raise ValueError(f"{product} 3x plan lacks {side} contract")
                if product == "IC" and not signal.get(f"v14_profit_{side}_security_id"):
                    raise ValueError("IC 3x plan lacks security id")
    elif signal.get("v14_profit_execution_day") is not None:
        raise ValueError(f"{product} 3x execution day lacks pending trigger")
