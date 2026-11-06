"""Read-only local report renderer. Files are not evidence of Codex publication.

The replay route validates a saved intraday sample at its recorded snapshot for
the *archived* microcap publication contract. It never grants live eligibility.
No quote fetch, strategy calculation, ledger mutation or mail code lives here.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
import types
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import build_ic_im_v1_4_digest as icim
import build_microcap_realtime_digest as micro
import ic_im_v1_4_delivery_contract as contract
import ic_im_v1_4_put_display as puts
import ic_im_intraday_observations as observations

BJ = ZoneInfo("Asia/Shanghai")
VERSIONS = ("v2.0", "v2.3", "v2.5")


def now_bj():
    return datetime.now(BJ)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def cell(value):
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return "N/A（结果缺少有效数值）"
    return f"{value:g}"


def stamp(value):
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError("snapshot/receipt clock must include timezone")
    return parsed.astimezone(BJ)


def strike_text(product, code):
    pattern = r"510500P\d{4}M(\d{5})" if product == "IC" else r"MO\d{4}-P-(\d+)"
    match = re.fullmatch(pattern, str(code or ""))
    if not match:
        return "N/A（当前Put合约缺失或无法解析）"
    return f"{int(match[1]) / 1000:.3f}元（按合约编码）" if product == "IC" else f"{int(match[1])}点（按合约编码）"


def _calendar(signal_day, anchor_day, next_session):
    if any(not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s) for s in (signal_day, anchor_day, next_session)):
        raise ValueError("sessions must be exact ISO dates")
    dates = {"market_date": date.fromisoformat(signal_day), "completed_day": date.fromisoformat(anchor_day),
             "verified_day": date.fromisoformat(anchor_day), "next_trade_day": date.fromisoformat(next_session)}
    contract.validate_session_dates(dates, "realtime")
    return dates


def _read_json(path, inputs):
    path = Path(path).resolve()
    data = path.read_bytes()
    inputs[str(path)] = sha(data)
    return json.loads(data)


def _check_readiness(readiness, signal_day, anchor_day):
    if (readiness.get("pass") is not True or readiness.get("mode") != "intraday"
            or readiness.get("expected_date") != anchor_day
            or readiness.get("source_and_shared_inputs_unchanged") is not True):
        raise ValueError("microcap readiness is not a successful same-anchor intraday receipt")
    start, end = stamp(readiness["started_at"]), stamp(readiness["completed_at"])
    if not (start <= end and start.date().isoformat() == end.date().isoformat() == signal_day and end.time() < time(15)):
        raise ValueError("microcap readiness clock/date/cutoff mismatch")
    stages = readiness.get("stages", [])
    if [s.get("stage") for s in stages] != ["preflight", "validate", "whole_check"]:
        raise ValueError("microcap readiness must contain the three original stages")
    for stage in stages:
        if stage.get("exit_code") != 0 or stage.get("status") != "passed" or stage.get("report", {}).get("ok") is not True:
            raise ValueError("microcap readiness stage failed or incomplete")
    pre = stages[0]["report"]
    members = pre.get("current_member_symbols", [])
    if len(members) != 100 or len(set(members)) != 100 or any(not re.fullmatch(r"\d{6}", str(s)) for s in members):
        raise ValueError("microcap members must contain 100 unique symbols")
    proof = pre.get("refresh_proof", {})
    evidence = proof.get("preflight_evidence", {})
    if (proof.get("target_end_date") != anchor_day or proof.get("verified_on") != signal_day
            or evidence.get("member_name_quote_day") != signal_day or evidence.get("member_name_count") != 100
            or evidence.get("current_st_name_intersection") != 0):
        raise ValueError("microcap member/ST/current-date proof mismatch")
    notice = evidence.get("current_member_notice_audit", {})
    if notice.get("signal_day") != signal_day or notice.get("previous_session") != anchor_day:
        raise ValueError("microcap notice evidence session mismatch")
    if not set(members) <= set(notice.get("symbols", [])):
        raise ValueError("notice observation does not cover all current members")
    return notice


def _micro_rows(paths, readiness, mode, signal_day, anchor_day, next_session, inputs, now):
    if set(paths) != set(VERSIONS):
        raise ValueError("microcap requires exactly v2.0/v2.3/v2.5 final CSVs")
    notice = _check_readiness(readiness, signal_day, anchor_day)
    rows, details = [], []
    for version in VERSIONS:
        path = Path(paths[version]).resolve()
        data = path.read_bytes(); inputs[str(path)] = sha(data)
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
        fields = list(reader)
        if len(fields) != 1 or not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError("final realtime CSV must have one row and unique columns")
        f = fields[0]
        for validator, args in ((micro.validate_strategy_identity, (version, f)),
                                (micro.validate_member_action_contract, (f,)),
                                (micro.validate_execution_contract, (version, f))):
            ok, reason = validator(*args)
            if not ok: raise ValueError(reason)
        snap = stamp(f.get("snapshot_time"))
        if snap > now:
            raise ValueError("microcap snapshot is in the future")
        # A private globals copy leaves the official module and concurrent live
        # callers untouched. Replay eligibility is never promoted to publication.
        publication_clock = snap if mode == "replay" else now
        validate = types.FunctionType(micro.validate_publication_contract.__code__,
                                      {**micro.validate_publication_contract.__globals__, "now_bj": lambda: publication_clock})
        ok, reason = validate("realtime", f)
        if not ok: raise ValueError(reason)
        if (f.get("date") != signal_day or f.get("quote_trade_date") != signal_day
                or f.get("latest_anchor_trade_date") != anchor_day
                or f.get("expected_latest_completed_trade_date") != anchor_day):
            raise ValueError("microcap CSV does not match requested signal/anchor sessions")
        for key in ("member_count", "member_price_count", "computed_member_price_count", "member_quote_trade_date_count", "member_quote_integrity_count"):
            if micro.parse_number(f.get(key, "")) != 100:
                raise ValueError(f"microcap {key} must equal 100")
        if f.get("quote_coverage") != "100/100":
            raise ValueError("microcap quote coverage must be 100/100")
        for key in ("member_quote_bad_symbols", "member_quote_integrity_bad_symbols"):
            if json.loads(f.get(key, "null")) != []:
                raise ValueError("microcap bad quote symbols are not empty")
        for key, nk in (("notice_audit_id", "audit_id"), ("notice_audit_manifest_sha256", "manifest_sha256"),
                        ("notice_observation_start", "observation_start"), ("notice_observation_end", "observation_end")):
            if not f.get(key) or f[key] != notice.get(nk):
                raise ValueError("microcap CSV/notice provenance mismatch: " + key)
        if stamp(f["notice_observation_end"]) > snap or snap < stamp(readiness["completed_at"]):
            raise ValueError("microcap snapshot precedes its readiness/notice evidence")
        item = {"version": version, "status": "OK", "fields": f, "csv_fields": f, "status_note": "", "exit_code": 0}
        action = micro.action_label(item)
        rows.append({"strategy": version, "revision": f["strategy_revision"], "signal_day": signal_day,
                     "next_session": next_session, "anchor_day": anchor_day, "snapshot": f["snapshot_time"],
                     "current": f["current_holding"], "target": f["next_holding"],
                     "scale_current": f["current_execution_scale"], "scale_target": f["next_session_actionable_scale"],
                     "action": action, "coverage": "100/100；100唯一；ST0"})
        details.append(item)
    _, body = micro.build_compact_digest(details, date_s=signal_day, run_url="", strategy_sha="见输入清单", publication_mode="realtime")
    return rows, body


def _icim_rows(payload, signal_day, anchor_day, next_session):
    if observations.matches(payload):
        if (payload.get("market_date") != signal_day or payload.get("completed_day") != anchor_day
                or payload.get("next_trade_day") != next_session):
            raise ValueError("ICIM observation does not match requested sessions")
        _, body, _ = observations.build(payload)
        return observations.rows(payload), body
    if payload.get("status") not in {"ok", "partial"} or payload.get("publication_mode") != "realtime":
        raise ValueError("local intraday renderer requires a successful/partial realtime ICIM result")
    if (payload.get("market_date") != signal_day or payload.get("completed_day") != anchor_day
            or payload.get("verified_day") != anchor_day or payload.get("next_trade_day") != next_session):
        raise ValueError("ICIM result does not match requested sessions")
    _, body, _ = icim.build_success(payload, "", "")  # Includes all formal payload gates and Put tables.
    rows = []
    for product in ("IC", "IM"):
        s = payload["signals"][product]
        core_code = puts.ic_contract(s, "current", "core") if product == "IC" else s.get("core_put_current_contract")
        qkey = "put_{}_total_qty" if product == "IC" else "total_put_{}_qty_normalized"
        actions = icim.action_parts(s, product)
        rows.append({"strategy": product, "revision": s["v14_build_id"], "signal_day": signal_day,
                     "next_session": next_session, "anchor_day": anchor_day,
                     "snapshot": s.get("signal_generated_at") or payload.get("report_generated_at"),
                     "current": s.get("total_units_current"), "target": s.get("total_units_target"),
                     "put_current": s.get(qkey.format("current")), "put_target": s.get(qkey.format("target")),
                     "put_strike": strike_text(product, core_code), "put_contract": core_code,
                     "action": "未确认（完整目标缺失）" if s.get("total_units_target") is None else "；".join(actions) or "无调整",
                     "iv_warning": icim.iv_warning_text(s), "fear": icim.fear_observation_text(payload)})
    # The official consumer's wording is for mail; rendering here never sends one.
    return rows, body.replace("本邮件是", "本地报告是")


def render(*, mode, signal_day, anchor_day, next_session, icim_result=None, microcap_csv=None, microcap_readiness=None):
    if mode not in {"intraday", "replay"}: raise ValueError("unsupported mode")
    inputs, rows, bodies = {}, [], []
    payload = _read_json(icim_result, inputs) if icim_result else None
    if (mode == "intraday" and isinstance(payload, dict) and observations.matches(payload)
            and (payload.get("saved_diagnostic_rerender") is True
                 or payload.get("signal_timing") == "saved_intraday_diagnostic_rerender")):
        raise ValueError("saved diagnostic rerender requires replay mode; no new intraday publication eligibility")
    if (next_session is None and not microcap_csv and payload is not None
            and observations.matches(payload) and payload.get("next_trade_day") is None
            and payload.get("calendar_warning")):
        observations.validate(payload)
        day, anchor = date.fromisoformat(signal_day), date.fromisoformat(anchor_day)
        if (not contract.is_session(day) or contract.adjacent_session(day, -1) != anchor
                or payload.get("market_date") != signal_day or payload.get("completed_day") != anchor_day):
            raise ValueError("observation signal/anchor sessions remain independently required")
    else:
        _calendar(signal_day, anchor_day, next_session)
    now = now_bj()
    if mode == "intraday" and now.date().isoformat() != signal_day:
        raise ValueError("live local report must use today's actual date")
    if microcap_csv:
        if not microcap_readiness: raise ValueError("microcap readiness receipt is required")
        readiness = _read_json(microcap_readiness, inputs)
        rr, body = _micro_rows(microcap_csv, readiness, mode, signal_day, anchor_day, next_session, inputs, now)
        rows += rr; bodies += ["## 微盘股", body]
    if icim_result:
        rr, body = _icim_rows(payload, signal_day, anchor_day, next_session)
        rows += rr; bodies += ["## IC / IM", body]
    if not rows: raise ValueError("at least one strategy result is required")
    banner = ("历史盘中快照只读重渲染；不是新的盘中信号，不具有今日发布资格。" if mode == "replay"
              else "盘中待收盘确认的下一交易日研究目标，非已完成交易或下单指令。文件生成不等于实际发布。")
    lines = [f"# {signal_day} 本地研究日报", "", banner, "", f"数据锚点 {anchor_day}；下一实际交易日 {next_session or 'N/A（正式日历未确认）'}。", "",
             "|策略|当前→目标|动作/状态|快照/生成证据|覆盖或Put保护|", "|---|---|---|---|---|"]
    for row in rows:
        if row["strategy"] in VERSIONS:
            transition = f"{micro.format_holding(row['current'])}→{micro.format_holding(row['target'])}；尺度{row['scale_current']}→{row['scale_target']}"
            extra = row["coverage"]
        else:
            transition = f"期货{number(row['current'])}→{number(row['target'])}；Put{number(row['put_current'])}→{number(row['put_target'])}（规范化）"
            extra = f"当前核心Put {row['put_contract'] or 'N/A'}；行权价{row['put_strike']}"
        lines.append("|" + "|".join(map(cell, [row["strategy"], transition, row["action"], row["snapshot"], extra])) + "|")
    for row in rows:
        if "iv_warning" in row: lines += ["", f"{row['strategy']}：{row['iv_warning']}"]
    if icim_result: lines += ["", rows[-1]["fear"]]
    summary = "\n".join(lines) + "\n"
    # Recheck exact bytes after all validation/rendering, never use mtime trust.
    if any(sha(Path(path).read_bytes()) != digest for path, digest in inputs.items()):
        raise ValueError("input changed during local rendering")
    if mode == "intraday" and microcap_csv and now_bj().time() >= time(15):
        raise ValueError("microcap report generation crossed 15:00")
    return {"summary": summary, "report": summary + "\n" + "\n\n".join(bodies) + "\n",
            "manifest": {"schema": 1, "mode": mode, "rendered_at": now_bj().isoformat(), "signal_day": signal_day,
                         "anchor_day": anchor_day, "next_session": next_session, "inputs_sha256": inputs,
                         "rows": rows, "actual_publication": False, "smtp_invoked": False,
                         "publication_status": "not_observed_by_renderer", "production_authorized": False,
                         "needs_actual_publication": mode == "intraday",
                         "publication_deadline": signal_day + "T15:00:00+08:00" if mode == "intraday" and microcap_csv else None,
                         "publication_deadline_exclusive": bool(mode == "intraday" and microcap_csv)}}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=("intraday", "replay"), required=True)
    p.add_argument("--signal-day", required=True); p.add_argument("--anchor-day", required=True)
    p.add_argument("--next-session"); p.add_argument("--icim-result")
    p.add_argument("--microcap-csv", action="append", default=[]); p.add_argument("--microcap-readiness")
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    csvs = {}
    for spec in args.microcap_csv:
        version, path = spec.split("=", 1)
        if version in csvs: raise ValueError("duplicate microcap version")
        csvs[version] = path
    result = render(mode=args.mode, signal_day=args.signal_day, anchor_day=args.anchor_day,
                    next_session=args.next_session, icim_result=args.icim_result, microcap_csv=csvs,
                    microcap_readiness=args.microcap_readiness)
    args.out.mkdir(parents=True, exist_ok=False)
    for name in ("summary", "report"):
        (args.out / (name + ".md")).write_text(result[name], encoding="utf-8")
    result["manifest"]["outputs_sha256"] = {name + ".md": sha((args.out / (name + ".md")).read_bytes()) for name in ("summary", "report")}
    (args.out / "manifest.json").write_text(json.dumps(result["manifest"], ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(args.out.resolve()), "actual_publication": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
