from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


HOLD_ACTIONS = {"", "HOLD", "NONE", "NO_ACTION", "MAINTAIN"}


def number(value: Any) -> str:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return str(value or "N/A")
    return f"{result:g}"


def action_parts(signal: dict[str, Any]) -> list[str]:
    labels = {
        "core_action": "核心合约",
        "momentum_action": "动量袖",
        "grid_action": "网格",
        "put_action": "Put",
        "call_action": "Call",
    }
    parts = []
    for key, label in labels.items():
        value = str(signal.get(key, "")).strip().upper()
        if value not in HOLD_ACTIONS:
            parts.append(f"{label} {value}")
    return parts


def put_text(product: str, signal: dict[str, Any], target: bool) -> str:
    prefix = "target" if target else "current"
    contract = signal.get(f"put_{prefix}_contract") or "无"
    if product == "IC":
        qty = signal.get(f"put_{prefix}_total_qty", 0)
    else:
        qty = signal.get(f"core_put_{prefix}_qty_normalized", 0)
    return f"{number(qty)}张 {contract}"


def call_text(signal: dict[str, Any], target: bool) -> str:
    prefix = "target" if target else "current"
    contract = signal.get(f"call_{prefix}_contract") or "无"
    if not target:
        qty = 2 if bool(signal.get("call_has_position")) else 0
    else:
        qty = signal.get("call_target_qty_normalized", 0)
    return f"{number(qty)}张 {contract}"


def build_success(payload: dict[str, Any], run_url: str, subject_prefix: str) -> tuple[str, str, bool]:
    signals = payload.get("signals", {})
    if set(signals) != {"IC", "IM"}:
        raise ValueError("result must contain IC and IM signals")
    actions = {product: action_parts(signals[product]) for product in ("IC", "IM")}
    actionable = any(actions.values())
    mode = str(payload.get("publication_mode", "close_confirmed"))
    realtime = mode == "realtime"
    tag = (
        ("预估需调整" if actionable else "预估无需调整")
        if realtime
        else ("需调整" if actionable else "无需调整")
    )
    mode_tag = "盘中实时" if realtime else "收盘确认"
    prefix = f"[{subject_prefix.strip().strip('[]')}]" if subject_prefix.strip() else ""
    day = str(
        payload.get("market_date" if realtime else "completed_day", "未知日期")
    )
    subject = f"{prefix}[{mode_tag}][{tag}] IC/IM 1.2 日报 - {day}"
    lines = [
        "## 今日结论",
        "",
        (
            "**盘中预估存在下一交易日调整，请等待收盘确认。**"
            if actionable and realtime
            else "**盘中暂未出现下一交易日调整，收盘前仍可能变化。**"
            if realtime
            else "**存在下一交易日调整，请查看逐腿变化。**"
            if actionable
            else "**IC、IM均无需调整。**"
        ),
        "",
        f"信号日：**{day}**｜下一交易日：**{payload.get('next_trade_day', 'N/A')}**",
        "",
        "| 品种 | 期货总仓 | 动量袖 | 网格 | Put | Call | 预估变化 |",
        "|---|---:|---:|---:|---|---|---|",
    ]
    for product in ("IC", "IM"):
        signal = signals[product]
        current_total = number(signal.get("total_units_current"))
        target_total = number(signal.get("total_units_target"))
        current_mom = number(signal.get("momentum_current_weight"))
        target_mom = number(signal.get("momentum_next_weight"))
        current_grid = number(signal.get("grid_current"))
        target_grid = number(signal.get("grid_target"))
        put = f"{put_text(product, signal, False)} → {put_text(product, signal, True)}"
        call = "明确禁止" if product == "IC" else f"{call_text(signal, False)} → {call_text(signal, True)}"
        action = "；".join(actions[product]) or "无调整"
        lines.append(
            f"| {product} | {current_total} → {target_total} | {current_mom} → {target_mom} | "
            f"{current_grid} → {target_grid} | {put} | {call} | {action} |"
        )
    lines += [
        "",
        "## 审计状态",
        "",
        f"- 构建：`{payload.get('build', 'N/A')}`",
        f"- 已核验账本日：`{payload.get('verified_day', 'N/A')}`",
        f"- 账本序号：`{payload.get('sequence', 'N/A')}`",
        f"- 账本摘要：`{str(payload.get('digest', ''))[:12]}`",
        f"- 本次补写交易日数：`{payload.get('advanced_sessions', 'N/A')}`",
        "",
        (
            "本邮件是盘中研究预估，使用实时/延时行情与上一已核验收盘账本；"
            "盘中值并未写入账本，必须等待收盘确认。它不是账户持仓，不会自动下单。完整逐腿解释见附件。"
            if realtime
            else "本邮件是研究审计信号，不是账户持仓，不会自动下单。完整逐腿解释见附件。"
        ),
    ]
    if run_url:
        lines.append(f"[查看GitHub运行记录]({run_url})")
    return subject, "\n".join(lines), actionable


def build_failure(payload: dict[str, Any], run_url: str, subject_prefix: str) -> tuple[str, str]:
    prefix = f"[{subject_prefix.strip().strip('[]')}]" if subject_prefix.strip() else ""
    day = str(payload.get("generated_at", ""))[:10] or "未知日期"
    realtime = str(payload.get("publication_mode", "close_confirmed")) == "realtime"
    subject = f"{prefix}[异常][{'盘中实时' if realtime else '收盘确认'}] IC/IM 1.2 日报 - {day}"
    body = "\n".join(
        [
            "## 今日结论",
            "",
            (
                "**IC/IM盘中实时信号生成失败，请勿依据旧邮件调整。**"
                if realtime
                else "**IC/IM收盘信号生成失败，请勿依据旧邮件调整。**"
            ),
            "",
            f"- 构建：`{payload.get('build', 'N/A')}`",
            f"- 错误：{payload.get('error_type', 'RuntimeError')}: {payload.get('error', '未知错误')}",
            "- 持久账本没有因本次失败而跳日或部分推进。",
            f"- [查看GitHub运行记录]({run_url})" if run_url else "",
        ]
    ).rstrip()
    return subject, body


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", required=True)
    parser.add_argument("--report", default="")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--subject-prefix", default="")
    args = parser.parse_args()

    payload = json.loads(Path(args.result).read_text(encoding="utf-8"))
    run_url = os.environ.get("GITHUB_RUN_URL", "")
    if payload.get("status") == "ok":
        subject, body, _ = build_success(payload, run_url, args.subject_prefix)
        report = args.report
        if not report and payload.get("report_file"):
            report = str(Path(args.result).resolve().parent / str(payload["report_file"]))
        attachment = report if report and Path(report).is_file() else None
    else:
        subject, body = build_failure(payload, run_url, args.subject_prefix)
        attachment = None
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "ic_im_v1_2_digest.md").write_text(body, encoding="utf-8")
    (out_dir / "metadata.json").write_text(
        json.dumps(
            {"subject": subject, "body": body, "attachment": attachment},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
