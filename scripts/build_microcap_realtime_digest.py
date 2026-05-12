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
    match = re.search(pattern, text, flags=re.I)
    return match.group(0).strip() if match else ""


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
    summary_hint = (
        extract_line(output, r"current_holding[^\n]*")
        or extract_line(output, r"next_holding[^\n]*")
        or extract_line(output, r"trade_state[^\n]*")
        or "详见附件中的 v1.4/v1.6/v1.8 原始实时信号输出。"
    )

    md = out_dir / f"microcap_realtime_signal_digest_{date_s}.md"
    lines = [
        f"# 微盘股实时信号对照日报 - {date_s}",
        "",
        "> 用途：这是自动化仓库中的对照版微盘股实时信号推送，用来和微盘股原仓库自己的 GitHub Actions 调度准点性做比较。",
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
            "微盘股实时信号对照版已生成。",
            f"计划时间：{args.planned}",
            f"实际启动：{started or '未记录'}",
            f"脚本退出码：{exit_code or '未记录'}",
            "完整输出见附件，可用于和微盘股原仓库 workflow 的触发时间比较。",
            f"Run URL：{run_url}",
        ]
    )
    meta = {
        "subject": f"微盘股实时信号对照日报 - {date_s}",
        "body": body,
        "attachment": str(md),
    }
    (out_dir / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
