from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


BJ = ZoneInfo("Asia/Shanghai")


def now_bj() -> datetime:
    return datetime.now(BJ)


def clean_output(text: str, max_len: int = 45000) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    if len(text) > max_len:
        return text[: max_len - 200].rstrip() + "\n\n[输出过长，后续内容已截断。完整原始输出见 workflow artifact。]"
    return text


def extract_line(text: str, pattern: str) -> str:
    match = re.search(pattern, text, flags=re.I | re.M)
    return match.group(0).strip() if match else ""


def extract_signal_summary(output: str) -> str:
    keys = [
        "strategy_version",
        "snapshot_time",
        "latest_anchor_trade_date",
        "quote_trade_date",
        "current_holding",
        "next_holding",
        "trade_state",
        "current_execution_scale",
        "official_close_confirmed_signal",
        "quote_coverage",
    ]
    lines = []
    for key in keys:
        line = extract_line(output, rf"^{re.escape(key)}[^\n]*")
        if line:
            lines.append(f"- {line}")
    if lines:
        return "\n".join(lines)
    return "详见附件中的 v2.0 原始实时信号输出。"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a Gmail-ready microcap realtime signal digest.")
    parser.add_argument("--result", required=True, help="Path to realtime_signal_result.txt")
    parser.add_argument("--out-dir", required=True, help="Output artifact directory")
    parser.add_argument("--planned", default="12:45 Asia/Shanghai")
    parser.add_argument("--started", default="")
    parser.add_argument("--exit-code", default="")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    result_path = Path(args.result)
    raw = result_path.read_text(encoding="utf-8", errors="replace") if result_path.exists() else "未找到实时信号输出文件。"
    output = clean_output(raw)

    date_s = now_bj().date().isoformat()
    finished = now_bj().strftime("%Y-%m-%d %H:%M:%S %Z")
    started = args.started or os.environ.get("STARTED_BJ", "")
    run_url = os.environ.get("GITHUB_RUN_URL", "")
    exit_code = args.exit_code or os.environ.get("SIGNAL_EXIT_CODE", "")
    summary_hint = extract_signal_summary(output)

    md = out_dir / f"microcap_realtime_signal_digest_{date_s}.md"
    lines = [
        f"# 微盘股 v2.0 实时信号日报 - {date_s}",
        "",
        "> 用途：这是自动化仓库中的微盘股 v2.0 实时信号推送，直接使用盘中/实时 quote 输出当日信号。",
        "",
        "## 调度审计",
        "",
        f"- 计划时间：{args.planned}",
        f"- 实际启动：{started or '未记录'}",
        f"- 完成时间：{finished}",
        f"- Workflow Run：{run_url or '未提供'}",
        f"- 脚本退出码：{exit_code or '未记录'}",
        "",
        "---",
        "",
        "## 信号摘要",
        "",
        summary_hint,
        "",
        "---",
        "",
        "## 原始实时信号输出",
        "",
        "```text",
        output,
        "```",
        "",
    ]
    md.write_text("\n".join(lines), encoding="utf-8")

    body = "\n".join(
        [
            "微盘股 v2.0 实时信号已生成。",
            f"计划时间：{args.planned}",
            f"实际启动：{started or '未记录'}",
            f"脚本退出码：{exit_code or '未记录'}",
            "完整 v2.0 实时输出见附件。",
            f"Run URL：{run_url}",
        ]
    )
    meta = {
        "subject": f"微盘股 v2.0 实时信号日报 - {date_s}",
        "body": body,
        "attachment": str(md),
    }
    (out_dir / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
