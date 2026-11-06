"""Render the independent member-daily result without promoting missing fields."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
from datetime import date
from pathlib import Path


SCHEMA = "member_daily_report_v1"
REVISIONS = {
    "v2.0": "plain_mom16_fixed1_20260904",
    "v2.3": "plain_lb25_hl2p5_r2off_no_overheat_20261029",
    "v2.5": "plain_lb20_hl3_entry0_exit0_20260905",
}


def render(result: dict, *, expected_date: str) -> dict:
    date.fromisoformat(expected_date)
    if (result.get("schema") != "member_daily_result_v1"
            or result.get("market_date") != expected_date
            or result.get("publication_mode") not in {"intraday", "close_confirmed"}):
        raise ValueError("Member daily result schema/date/mode mismatch")
    versions = result.get("versions")
    if not isinstance(versions, list) or len(versions) != 3:
        raise ValueError("Member daily result needs three explicit version statuses")
    by_version = {row.get("version"): row for row in versions}
    if len(by_version) != 3 or set(by_version) != set(REVISIONS):
        raise ValueError("Member daily version identity mismatch")
    rows, verified = [], 0
    for version, revision in REVISIONS.items():
        item = by_version[version]
        if item.get("revision") != revision or item.get("status") not in {"verified", "partial", "reference"}:
            raise ValueError("Member daily revision/status mismatch")
        signal = item.get("signal")
        if item["status"] == "verified":
            if not isinstance(signal, dict):
                raise ValueError("Verified member daily row has no signal")
            required = ("current_holding", "next_holding")
            if any(signal.get(key) in (None, "") for key in required):
                raise ValueError("Verified signal has missing holding identity")
            if (signal.get('strategy_revision') != revision or signal.get('quote_trade_date') != expected_date
                    or signal.get('quote_coverage') != '100/100'):
                raise ValueError('Verified signal revision/date/member coverage differs')
            verified += 1
            scale = signal.get("next_session_target_scale", "N/A")
            text = f"{signal['current_holding']} → {signal['next_holding']}；目标尺度 {scale}"
        elif item["status"] == "reference":
            text = "旧篮子观察，新一期名单未交接；下一正式目标未确认。"
            if isinstance(signal, dict):
                text += f" 观察结果：{signal.get('current_holding', 'N/A')} → {signal.get('next_holding', 'N/A')}"
        else:
            if not isinstance(item.get("reason"), str) or not item["reason"].strip():
                raise ValueError("Partial member row must explain its missing information")
            text = "未确认：" + item["reason"]
        rows.append({"version": version, "revision": revision, "status": item["status"], "text": text})
    status = "OK" if verified == 3 else "PARTIAL"
    timing = "收盘确认" if result["publication_mode"] == "close_confirmed" else "盘中预估，待收盘确认"
    body = f"微盘股每日研究报告｜{expected_date}｜{timing}\n\n"
    body += "\n".join(f"{row['version']} [{row['revision']}] {row['text']}" for row in rows)
    body += ("\n\n成员行情覆盖：" + str(result.get('quote_coverage', '未提供'))
             + "；历史计算锚点：" + str(result.get('anchor_date', '未提供'))
             + "；本次结果生成：" + str(result.get('generated_at', '未提供')) + "。")
    missing = result.get('missing_member_symbols')
    if isinstance(missing, list) and missing:
        body += "\n行情缺项：" + "、".join(map(str, missing)) + "。"
    warnings = result.get("warnings", [])
    if not isinstance(warnings, list) or not all(isinstance(x, str) for x in warnings):
        raise ValueError("Warnings must be explicit strings")
    if warnings:
        body += "\n\n资料与数据提示：\n" + "\n".join(warnings)
    maintenance = result.get("maintenance")
    maintenance_date = maintenance.get("last_updated") if isinstance(maintenance, dict) else None
    body += "\n\n资料维护最后更新：" + str(maintenance_date or "未核验，未绑定本次维护回执")
    body += "。本期名单的历史核验不代表今日公告/ST资料已经完成核验。\n"
    body += "研究目标与模型记录，不代表账户成交。\n"
    label = "微盘股资料报告（未形成当日完整信号）" if verified == 0 else "微盘股日报"
    return {"schema": SCHEMA, "status": status, "signal_date": expected_date,
            "publication_mode": "realtime" if result["publication_mode"] == "intraday" else "close_confirmed",
            "subject": f"[{timing}]{'[部分信息]' if status == 'PARTIAL' else ''}{label} {expected_date}",
            "body": body, "html_body": "<pre>" + html.escape(body) + "</pre>",
            "versions": rows, "warnings": warnings}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--expected-date", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    raw = args.result.read_bytes()
    metadata = render(json.loads(raw), expected_date=args.expected_date)
    metadata["result_sha256"] = hashlib.sha256(raw).hexdigest()
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.out / "report.md").write_text(metadata["body"], encoding="utf-8")
    (args.out / "report.html").write_text(metadata["html_body"], encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
