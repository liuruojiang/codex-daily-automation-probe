"""Independent offline date and quantity checks for modern IC/IM delivery.

The calendar below is the published common CFFEX/SSE 2026 calendar already
used by the official strategy.  Its source is
liuruojiang/ic-im-rolling-arbitrage@739071a201506dd869b9a156e490c3bafd95a334,
poe_ic_im_mainline_v1_4_bot.py::_EXCHANGE_CLOSURES and
_OFFICIAL_EXCHANGE_CALENDAR_YEARS.  This is an explicit covered-year snapshot,
never a NAV-derived or weekday-only calendar.  New years fail closed until
their official schedule is added and checked against the strategy calendar.
"""
from datetime import date, timedelta
import math
from typing import Any


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
        if (signal.get("v14_route_state", "future") != "future"
                or signal.get("v14_profit_pending") or signal.get("option_monthly_reset_due")):
            raise ValueError(f"{product} ordinary Put plan conflicts with core route, 3x plan or monthly maintenance")
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
