"""Presentation-only IC/IM observations; never a ledger or execution contract."""
from __future__ import annotations

import html
import math
import re
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

REVISION = "ic_im_intraday_observations_20261106_v1"
CALENDAR_PARTIAL_REVISION = "ic_im_new_put_calendar_partial_20261106_v1"
STATUSES = {"verified", "partial", "unverified", "unavailable"}


def matches(payload):
    return payload.get("publication_contract_revision") == REVISION


def numeric(value):
    try:
        return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except (OverflowError, ValueError):
        return False


def number(value):
    return f"{value:g}" if numeric(value) else "N/A"


def validate(payload):
    if not matches(payload) or payload.get("status") != "partial" or payload.get("publication_mode") != "realtime":
        raise ValueError("unsupported intraday observation envelope")
    for key in ("ledger_recovery_eligible", "persistence_eligible", "production_authorized",
                "complete_portfolio_target", "actual_publication"):
        if payload.get(key) is not False:
            raise ValueError(f"observation {key} must be false")
    days = {key: date.fromisoformat(str(payload.get(key))) for key in
            ("market_date", "completed_day", "verified_day")}
    next_day = date.fromisoformat(str(payload["next_trade_day"])) if payload.get("next_trade_day") else None
    if (not days["verified_day"] <= days["completed_day"] < days["market_date"]
            or (next_day is not None and next_day <= days["market_date"])
            or (next_day is None and not payload.get("calendar_warning"))):
        raise ValueError("observation dates must retain the actual prior ledger anchor")
    clocks = {}
    for key in ("generated_at", "report_generated_at"):
        stamp = datetime.fromisoformat(str(payload.get(key)))
        if stamp.tzinfo is None:
            raise ValueError("observation clock must include timezone")
        clocks[key] = stamp.astimezone(ZoneInfo("Asia/Shanghai"))
    query, report = clocks["generated_at"], clocks["report_generated_at"]
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    if not query <= report <= now:
        raise ValueError("observation clocks must be ordered and not in the future")
    if query.date() != days["market_date"] or not (
            time(9, 30) <= query.time() < time(11, 30) or time(13) <= query.time() < time(15)):
        raise ValueError("observation query must retain its actual same-day intraday clock")
    rows = payload.get("product_observations")
    if not isinstance(rows, dict) or set(rows) != {"IC", "IM"}:
        raise ValueError("observation report requires separate IC and IM status")
    for product, row in rows.items():
        if not isinstance(row, dict) or row.get("status") not in STATUSES:
            raise ValueError("invalid product observation status")
        signal = row.get("signal")
        if row["status"] == "unavailable":
            if signal is not None:
                raise ValueError("unavailable observation cannot carry a signal")
            continue
        if not isinstance(signal, dict):
            raise ValueError("observation signal must be an object")
        if row["status"] in {"unverified", "unavailable"}:
            continue
        if (payload.get("ledger_unchanged") is not True or payload["verified_day"] != payload["completed_day"]
                or next_day is None
                or row.get("validation_error") is not None
                or signal.get("product") != product or str(signal.get("market_date"))[:10] != payload["market_date"]
                or signal.get("close_confirmed") is not False or signal.get("market_phase") != "盘中"
                or str(signal.get("state_anchor_day"))[:10] != payload["completed_day"]):
            raise ValueError("qualified observation identity/date/ledger binding is inconsistent")
        if signal.get("diagnostic_only") is True:
            raise ValueError("diagnostic observation cannot be a confirmed target")
        if (str(signal.get("next_trade_date"))[:10] != payload.get("next_trade_day")
                or str(signal.get("future_quote_date"))[:10] != payload["market_date"]):
            raise ValueError("qualified future quote/session dates must match the envelope")
        if row["status"] == "partial":
            if (product != "IM" or signal.get("calendar_partial_revision") != CALENDAR_PARTIAL_REVISION
                    or signal.get("futures_target_confirmed") is not True
                    or signal.get("calendar_provisional") is not True
                    or signal.get("signal_complete") is not False
                    or signal.get("persistence_eligible") is not False
                    or signal.get("unconfirmed_put_legs") != ["momentum"]
                    or signal.get("momentum_put_target_contract") is not None
                    or signal.get("momentum_put_target_qty_normalized") is not None
                    or signal.get("total_put_target_qty_normalized") is not None):
                raise ValueError("calendar partial must retain missing Put identity and protection status")
            weight, mom, required = (signal.get(k) for k in
                ("momentum_next_weight", "momentum_120", "momentum_put_required_qty_normalized"))
            if (not all(numeric(v) for v in (weight, mom, required)) or not 0 < weight <= 1
                    or mom >= 0 or not math.isclose(required, 3 * .5 * weight, abs_tol=1e-12)):
                raise ValueError("calendar partial Put requirement must follow MOM120 and momentum weight")
            evidence = signal.get("future_quote_evidence")
            if (not isinstance(evidence, dict) or not isinstance(signal.get("future_quote_source"), str)
                    or not signal["future_quote_source"].strip()
                    or evidence.get("source") != signal["future_quote_source"]
                    or evidence.get("contract") != signal.get("core_current")
                    or not isinstance(evidence.get("contract"), str) or not evidence["contract"]
                    or str(evidence.get("source_date"))[:10] != payload["market_date"]
                    or str(evidence.get("row_source_date"))[:10] != payload["market_date"]
                    or evidence.get("source_snapshot_kind") not in {
                        "http_last_modified_whole_frame", "latest_available_contract_update"}
                    or not numeric(evidence.get("lastprice")) or evidence["lastprice"] <= 0
                    or not numeric(signal.get("future_last"))
                    or not math.isclose(evidence["lastprice"], signal["future_last"], rel_tol=0, abs_tol=1e-12)):
                raise ValueError("calendar partial future source/contract/price evidence is not bound")
            collected = datetime.fromisoformat(str(evidence.get("collected_at")))
            snapshot = datetime.fromisoformat(str(evidence.get("source_snapshot_at")))
            if collected.tzinfo is None or snapshot.tzinfo is None:
                raise ValueError("calendar partial future evidence clocks require timezone")
            collected, snapshot = (v.astimezone(ZoneInfo("Asia/Shanghai")) for v in (collected, snapshot))
            if (not query <= collected <= report or snapshot > collected
                    or collected.date() != days["market_date"] or snapshot.date() != days["market_date"]
                    or collected.time() >= time(15) or snapshot.time() >= time(15)):
                raise ValueError("calendar partial future evidence is wrong-day, future or past cutoff")
        else:
            if (signal.get("calendar_provisional") is True or signal.get("calendar_partial_revision")
                    or signal.get("signal_complete") is not True or signal.get("persistence_eligible") is not True
                    or signal.get("unconfirmed_put_legs") or signal.get("incomplete_components")
                    or signal.get("futures_target_confirmed", True) is not True):
                raise ValueError("unconfirmed model cannot be relabelled verified")
            from check_ic_im_v1_4_delivery import expected_identity_for_day
            expected_build, expected_delivery = expected_identity_for_day(days["market_date"])
            if (signal.get("strategy_version") != "1.4" or signal.get("strategy_revision") != "r1"
                    or signal.get("v14_build_id") != expected_build
                    or payload.get("build") != expected_build or payload.get("delivery_revision") != expected_delivery):
                raise ValueError("verified observation requires the formal producer identity")
            fields = ([f"put_{when}_{leg}_qty" for when in ("current", "target")
                       for leg in ("core", "momentum", "grid", "total")] if product == "IC" else
                      [f"{leg}_put_{when}_qty_normalized" for when in ("current", "target")
                       for leg in ("core", "momentum", "total")])
            if any(not numeric(signal.get(k)) or signal[k] < 0 for k in fields):
                raise ValueError("verified observation cannot contain an unknown Put quantity")
            from ic_im_v1_4_put_display import ic_contract
            for when in ("current", "target"):
                for leg in ("core", "momentum"):
                    qty = signal[f"put_{when}_{leg}_qty"] if product == "IC" else signal[f"{leg}_put_{when}_qty_normalized"]
                    code = ic_contract(signal, when, leg) if product == "IC" else signal.get(f"{leg}_put_{when}_contract")
                    pattern = r"510500P[0-9]{4}M[0-9]{5}" if product == "IC" else r"MO[0-9]{4}-P-[0-9]+"
                    if qty > 0 and re.fullmatch(pattern, str(code or "")) is None:
                        raise ValueError("verified observation cannot contain an unknown positive Put contract")
        from ic_im_v1_4_delivery_contract import validate_session_dates
        validate_session_dates(days | {"next_trade_day": next_day}, "realtime")
        for key in ("signal_generated_at", "signal_calculation_started_at"):
            if signal.get(key) is not None:
                stamp = datetime.fromisoformat(str(signal[key]))
                if stamp.tzinfo is None or not query <= stamp <= report:
                    raise ValueError("qualified signal clocks are not bound to this query/report")
        for when in ("current", "target"):
            fields = (f"core_units_{when}", f"momentum_units_{when}", f"grid_{when}", f"total_units_{when}")
            values = [signal.get(k) for k in fields]
            if not all(numeric(v) and v >= 0 for v in values):
                raise ValueError("qualified futures quantities must be finite and nonnegative")
            if not math.isclose(sum(values[:3]), values[3], abs_tol=1e-12):
                raise ValueError("qualified futures total differs from its independently reported legs")
            weight = signal.get("momentum_current_weight" if when == "current" else "momentum_next_weight")
            if not numeric(weight) or not 0 <= weight <= 1 or not math.isclose(values[1], .5 * weight, abs_tol=1e-12):
                raise ValueError("qualified momentum units differ from 0.5 times weight")
    if not isinstance(payload.get("source_report"), str):
        raise ValueError("observation source report must be text")


def rows(payload):
    validate(payload)
    result = []
    for product in ("IC", "IM"):
        item = payload["product_observations"][product]
        signal = item.get("signal") or {}
        qualified = item["status"] in {"verified", "partial"}
        core_code = (signal.get("v14_core_put_current_contract") or signal.get("put_current_contract")
                     if product == "IC" else signal.get("core_put_current_contract"))
        current_key = "put_current_total_qty" if product == "IC" else "total_put_current_qty_normalized"
        target_key = "put_target_total_qty" if product == "IC" else "total_put_target_qty_normalized"
        iv = signal.get("iv_warning")
        iv_text = iv.get("text") if isinstance(iv, dict) else None
        current, target = (signal.get("total_units_current"), signal.get("total_units_target")) if qualified else (None, None)
        momentum_current, momentum_target = (signal.get("momentum_units_current"), signal.get("momentum_units_target")) if qualified else (None, None)
        action = ("动量开仓研究目标" if numeric(momentum_current) and numeric(momentum_target) and momentum_target > momentum_current
                  else "已核验研究目标" if qualified else "诊断观察，目标未确认")
        if item["status"] == "partial":
            action += "；Put合约N/A，保护未落实"
        result.append({"strategy": product, "revision": signal.get("v14_build_id") or payload.get("build"),
            "signal_day": payload["market_date"], "next_session": payload["next_trade_day"],
            "anchor_day": payload["verified_day"], "snapshot": signal.get("signal_generated_at") or payload["report_generated_at"],
            "current": current, "target": target, "put_current": signal.get(current_key) if qualified else None,
            "put_target": signal.get(target_key) if qualified else None, "put_contract": core_code if qualified else None,
            "put_strike": "N/A（缺少独立展示证据）", "action": action,
            "momentum_current": momentum_current, "momentum_target": momentum_target,
            "protection_required": signal.get("momentum_put_required_qty_normalized"),
            "observation_status": item["status"], "validation_error": item.get("validation_error"),
            "iv_warning": "IV：" + str(iv_text or "N/A（本次未核验）"),
            "fear": "恐慌读数：" + number(signal.get("fear_greed_index")) + "；数据日 " + str(signal.get("fear_data_date") or "N/A")})
    return result


def build(payload, subject_prefix=""):
    rr = rows(payload)
    prefix = f"[{subject_prefix.strip().strip('[]')}]" if subject_prefix.strip() else ""
    subject = f"{prefix}[盘中信息][部分资料] IC/IM研究日报 - {payload['market_date']}"
    lines = ["## IC/IM 盘中信息报告", "", "已取得的信息逐项展示；缺项不代表零仓位。研究目标待收盘确认，非已完成交易。",
             "Put合约未确认不代表无需保护或保护已落实。", "", f"实际账本锚点：{payload['verified_day']}；下一交易日：{payload.get('next_trade_day') or 'N/A（正式日历未确认）'}。",
             "", "|品种|资料状态|动量期货当前→目标|期货总量当前→目标|Put保护|", "|---|---|---|---|---|"]
    if payload.get("saved_diagnostic_rerender") is True or payload.get("signal_timing") == "saved_intraday_diagnostic_rerender":
        lines[2:2] = ["**今日盘中已采集信息的补充整理；非新盘中采集，不具有新的盘中发布资格。**", ""]
        subject = subject.replace("[盘中信息]", "[盘中资料补充整理]")
    for row in rr:
        protection = (f"需要{number(row['protection_required'])}张（规范化）；合约N/A，保护未落实"
                      if row["observation_status"] == "partial" else
                      f"当前{number(row['put_current'])}→目标{number(row['put_target'])}张（规范化）" if row["observation_status"] == "verified"
                      else "N/A（未核验）")
        lines.append(f"|{row['strategy']}|{row['observation_status']}|{number(row['momentum_current'])}→{number(row['momentum_target'])}|{number(row['current'])}→{number(row['target'])}|{protection}|")
        if row["validation_error"]:
            lines += ["", f"{row['strategy']}提示：{row['validation_error']}"]
        lines += ["", row["strategy"] + "：" + row["iv_warning"] + "；" + row["fear"]]
    lines += ["", "### 本次原始查询记录（未统一核验，仅供复核）", "", "以下保留原始文字；其中未确认的数字不能替代上表已核验目标。", ""]
    lines += [f"原记录校验状态：{product}={payload['product_observations'][product]['status']}；"
              + str(payload['product_observations'][product].get('validation_error') or '仅按上表资格展示')
              for product in ("IC", "IM")]
    lines += ["> " + line for line in payload["source_report"].splitlines()]
    return subject, "\n".join(lines) + "\n", any(numeric(r["momentum_target"]) and numeric(r["momentum_current"]) and r["momentum_target"] != r["momentum_current"] for r in rr)


def build_html(payload):
    subject, body, _ = build(payload)
    return "<!doctype html><html><body><h1>" + html.escape(subject) + "</h1><pre style='white-space:pre-wrap'>" + html.escape(body) + "</pre></body></html>"
