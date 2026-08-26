from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
from typing import Any


HOLD_ACTIONS = {"", "HOLD", "NONE", "NO_ACTION", "MAINTAIN", "WAIT_IV"}
ACTION_CN = {
    "HOLD": "维持",
    "NONE": "无",
    "NO_ACTION": "无调整",
    "MAINTAIN": "维持",
    "OPEN": "开仓",
    "CLOSE": "平仓",
    "ROLL": "展期",
    "RESIZE": "调整",
    "RESET": "重置",
    "BUY": "买入",
    "SELL": "卖出",
    "TURN_ON": "开启",
    "TURN_OFF": "关闭",
    "RESCUE": "救援换仓",
    "WAIT_IV": "等待IV条件（无需操作）",
}


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


def escaped(value: Any) -> str:
    return html.escape(str(value if value is not None else "N/A"), quote=True)


def action_cn(value: Any) -> str:
    normalized = str(value or "HOLD").strip().upper()
    return ACTION_CN.get(normalized, normalized)


def product_action_text(signal: dict[str, Any]) -> str:
    labels = {
        "core_action": "核心",
        "momentum_action": "动量",
        "grid_action": "网格",
        "put_action": "Put",
        "call_action": "Call",
    }
    parts = []
    for key, label in labels.items():
        value = str(signal.get(key, "")).strip().upper()
        if value not in HOLD_ACTIONS:
            parts.append(f"{label}：{action_cn(value)}")
    return "；".join(parts) or "维持现状"


def leg_row(label: str, current: Any, target: Any, note: str = "") -> str:
    note_html = (
        f'<div style="margin-top:4px;color:#667085;font-size:12px;line-height:1.45;">{escaped(note)}</div>'
        if note
        else ""
    )
    return f'''<tr>
      <td style="padding:11px 10px;border-top:1px solid #edf0f5;color:#475467;font-size:13px;vertical-align:top;width:30%;">{escaped(label)}</td>
      <td style="padding:11px 10px;border-top:1px solid #edf0f5;color:#101828;font-size:14px;line-height:1.5;vertical-align:top;">
        <span style="color:#667085;">{escaped(current)}</span>
        <span style="padding:0 7px;color:#98a2b3;">→</span>
        <strong style="color:#101828;">{escaped(target)}</strong>{note_html}
      </td>
    </tr>'''


def product_card(product: str, signal: dict[str, Any], actionable: bool) -> str:
    title = "IC / 中证500" if product == "IC" else "IM / 中证1000"
    accent = "#f79009" if actionable else "#12b76a"
    badge_bg = "#fff4e5" if actionable else "#ecfdf3"
    badge_text = product_action_text(signal)
    current_total = number(signal.get("total_units_current"))
    target_total = number(signal.get("total_units_target"))
    core_current = signal.get("core_current") or "当前核心合约"
    core_target = signal.get("core_target") or core_current
    momentum_current = f"权重 {number(signal.get('momentum_current_weight'))}"
    momentum_target = f"权重 {number(signal.get('momentum_next_weight'))}"
    grid_current = f"{number(signal.get('grid_current'))}倍"
    grid_target = f"{number(signal.get('grid_target'))}倍"
    put_current = put_text(product, signal, False)
    put_target = put_text(product, signal, True)
    call_current = "禁止卖Call" if product == "IC" else call_text(signal, False)
    call_target = "禁止卖Call" if product == "IC" else call_text(signal, True)
    rows = "".join(
        [
            leg_row("核心期货", core_current, core_target, f"动作：{action_cn(signal.get('core_action'))}"),
            leg_row("动量袖", momentum_current, momentum_target, f"动作：{action_cn(signal.get('momentum_action'))}"),
            leg_row("估值网格", grid_current, grid_target, f"动作：{action_cn(signal.get('grid_action'))}"),
            leg_row("Put保护", put_current, put_target, f"动作：{action_cn(signal.get('put_action'))}"),
            leg_row("Call", call_current, call_target, "IC明确禁止Call" if product == "IC" else f"动作：{action_cn(signal.get('call_action'))}"),
        ]
    )
    return f'''<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:0 0 16px;background:#ffffff;border:1px solid #e4e7ec;border-radius:14px;overflow:hidden;border-collapse:separate;">
  <tr>
    <td style="padding:18px 18px 14px;border-left:5px solid {accent};">
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0">
        <tr>
          <td style="font-size:19px;font-weight:750;color:#101828;">{escaped(title)}</td>
          <td align="right"><span style="display:inline-block;padding:5px 9px;border-radius:999px;background:{badge_bg};color:{accent};font-size:12px;font-weight:700;">{escaped(badge_text)}</span></td>
        </tr>
      </table>
      <div style="margin-top:14px;color:#667085;font-size:12px;">期货总仓</div>
      <div style="margin-top:3px;font-size:24px;line-height:1.2;color:#101828;font-weight:760;">{escaped(current_total)}倍 <span style="color:#98a2b3;font-weight:500;">→</span> <span style="color:{accent};">{escaped(target_total)}倍</span></div>
    </td>
  </tr>
  <tr><td style="padding:0 8px 8px;"><table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;">{rows}</table></td></tr>
</table>'''


def build_success_html(payload: dict[str, Any], run_url: str) -> str:
    signals = payload.get("signals", {})
    if set(signals) != {"IC", "IM"}:
        raise ValueError("result must contain IC and IM signals")
    actions = {product: action_parts(signals[product]) for product in ("IC", "IM")}
    actionable = any(actions.values())
    realtime = str(payload.get("publication_mode", "close_confirmed")) == "realtime"
    day = str(payload.get("market_date" if realtime else "completed_day", "未知日期"))
    mode_text = "盘中实时预估" if realtime else "收盘确认"
    headline = "存在预估调整" if actionable and realtime else "盘中暂时无需调整" if realtime else "存在下一交易日调整" if actionable else "无需调整"
    warning = (
        "盘中结果会随行情变化，必须等待收盘确认；本邮件不会自动下单。"
        if realtime
        else "这是研究审计信号，不是账户持仓，也不会自动下单。"
    )
    accent = "#f79009" if actionable else "#12b76a"
    banner_bg = "#fff7ed" if actionable else "#ecfdf3"
    link = (
        f'<a href="{escaped(run_url)}" style="color:#175cd3;text-decoration:none;font-weight:650;">查看GitHub运行记录 →</a>'
        if run_url
        else ""
    )
    cards = "".join(
        product_card(product, signals[product], bool(actions[product]))
        for product in ("IC", "IM")
    )
    preheader = f"{mode_text}｜{headline}｜信号日 {day}"
    return f'''<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f2f4f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',Arial,sans-serif;color:#101828;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{escaped(preheader)}</div>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f2f4f7;"><tr><td align="center" style="padding:20px 10px;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:680px;">
  <tr><td style="padding:24px 22px;background:#101828;border-radius:16px 16px 0 0;color:#ffffff;">
    <div style="font-size:12px;letter-spacing:.08em;color:#b9c2d0;font-weight:700;">IC / IM 1.2 · {escaped(mode_text)}</div>
    <div style="margin-top:8px;font-size:27px;line-height:1.25;font-weight:780;">{escaped(headline)}</div>
    <div style="margin-top:10px;color:#d0d5dd;font-size:14px;line-height:1.55;">信号日 {escaped(day)} · 下一交易日 {escaped(payload.get('next_trade_day', 'N/A'))}</div>
  </td></tr>
  <tr><td style="padding:18px 18px 2px;background:#f8fafc;">
    <div style="margin-bottom:16px;padding:14px 15px;background:{banner_bg};border:1px solid {accent}33;border-radius:12px;color:#344054;font-size:14px;line-height:1.6;"><strong style="color:{accent};">{escaped(headline)}</strong><br>{escaped(warning)}</div>
    {cards}
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:2px 0 16px;background:#ffffff;border:1px solid #e4e7ec;border-radius:12px;">
      <tr><td style="padding:15px 16px;color:#667085;font-size:12px;line-height:1.65;">
        <strong style="color:#344054;">审计状态</strong><br>
        已核验账本日 {escaped(payload.get('verified_day', 'N/A'))} · 序号 {escaped(payload.get('sequence', 'N/A'))} · 摘要 {escaped(str(payload.get('digest', ''))[:12])}<br>
        构建 {escaped(payload.get('build', 'N/A'))} · 本次补写 {escaped(payload.get('advanced_sessions', 'N/A'))} 个交易日
      </td></tr>
    </table>
  </td></tr>
  <tr><td style="padding:16px 20px 22px;background:#ffffff;border-radius:0 0 16px 16px;color:#667085;font-size:12px;line-height:1.6;">
    完整逐腿解释见附件。{link}
  </td></tr>
</table>
</td></tr></table>
</body></html>'''


def build_failure_html(payload: dict[str, Any], run_url: str) -> str:
    realtime = str(payload.get("publication_mode", "close_confirmed")) == "realtime"
    mode_text = "盘中实时" if realtime else "收盘确认"
    link = (
        f'<p style="margin:16px 0 0;"><a href="{escaped(run_url)}" style="color:#175cd3;text-decoration:none;font-weight:650;">查看GitHub运行记录 →</a></p>'
        if run_url
        else ""
    )
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:24px 10px;background:#f2f4f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',Arial,sans-serif;color:#101828;">
<div style="max-width:640px;margin:0 auto;background:#ffffff;border:1px solid #fecdca;border-radius:16px;overflow:hidden;">
  <div style="padding:22px;background:#b42318;color:#ffffff;"><div style="font-size:12px;font-weight:700;">IC / IM 1.2 · {escaped(mode_text)}</div><div style="margin-top:8px;font-size:24px;font-weight:760;">信号生成失败</div></div>
  <div style="padding:20px;color:#344054;font-size:14px;line-height:1.7;"><strong>请勿依据旧邮件调整。</strong><p>错误：{escaped(payload.get('error_type', 'RuntimeError'))}: {escaped(payload.get('error', '未知错误'))}</p><p>持久账本没有因本次失败而跳日或部分推进。</p>{link}</div>
</div></body></html>'''


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
        html_body = build_success_html(payload, run_url)
        report = args.report
        if not report and payload.get("report_file"):
            report = str(Path(args.result).resolve().parent / str(payload["report_file"]))
        attachment = report if report and Path(report).is_file() else None
    else:
        subject, body = build_failure(payload, run_url, args.subject_prefix)
        html_body = build_failure_html(payload, run_url)
        attachment = None
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "ic_im_v1_2_digest.md").write_text(body, encoding="utf-8")
    (out_dir / "ic_im_v1_2_digest.html").write_text(html_body, encoding="utf-8")
    (out_dir / "metadata.json").write_text(
        json.dumps(
            {
                "subject": subject,
                "body": body,
                "html_body": html_body,
                "attachment": attachment,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
