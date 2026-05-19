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
        "base_version",
        "signal_model",
        "overlay",
        "snapshot_time",
        "latest_anchor_trade_date",
        "quote_trade_date",
        "current_holding",
        "next_holding",
        "trade_state",
        "holding_trade_state",
        "scale_trade_state",
        "current_execution_scale",
        "target_vol_current_execution_scale",
        "target_vol_next_execution_scale",
        "official_close_confirmed_signal",
        "annualized_log_wls_score",
        "log_wls_r2",
        "quote_source",
        "quote_coverage",
    ]
    lines = []
    for key in keys:
        line = extract_line(output, rf"^{re.escape(key)}[^\n]*")
        if line:
            lines.append(f"- {line}")
    if lines:
        return "\n".join(lines)
    return "详见附件中的原始实时信号输出。"


def split_version_spec(spec: str, default_version: str) -> tuple[str, str]:
    if "=" not in spec:
        return default_version, spec
    version, value = spec.split("=", 1)
    version = version.strip()
    if not version:
        raise ValueError(f"empty version in spec: {spec!r}")
    return version, value.strip()


def parse_exit_codes(specs: list[str]) -> tuple[dict[str, str], str]:
    mapped: dict[str, str] = {}
    default = ""
    for spec in specs:
        version, value = split_version_spec(spec, "")
        if version:
            mapped[version] = value
        else:
            default = value
    return mapped, default


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a Gmail-ready microcap realtime signal digest.")
    parser.add_argument(
        "--result",
        action="append",
        required=True,
        help="Path to realtime signal output, or version=path. Can be repeated.",
    )
    parser.add_argument("--out-dir", required=True, help="Output artifact directory")
    parser.add_argument("--planned", default="12:45 Asia/Shanghai")
    parser.add_argument("--started", default="")
    parser.add_argument("--exit-code", action="append", default=[], help="Exit code, or version=code. Can be repeated.")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    exit_codes, default_exit_code = parse_exit_codes(args.exit_code)
    result_specs = [split_version_spec(spec, "v2.0" if len(args.result) == 1 else "") for spec in args.result]
    results: list[dict[str, str]] = []
    for version, result_value in result_specs:
        if not version:
            raise ValueError("multiple --result values must use version=path format")
        result_path = Path(result_value)
        raw = result_path.read_text(encoding="utf-8", errors="replace") if result_path.exists() else "未找到实时信号输出文件。"
        output = clean_output(raw)
        exit_code = exit_codes.get(version, default_exit_code)
        results.append(
            {
                "version": version,
                "path": str(result_path),
                "output": output,
                "summary": extract_signal_summary(output),
                "exit_code": exit_code,
            }
        )

    date_s = now_bj().date().isoformat()
    finished = now_bj().strftime("%Y-%m-%d %H:%M:%S %Z")
    started = args.started or os.environ.get("STARTED_BJ", "")
    run_url = os.environ.get("GITHUB_RUN_URL", "")
    if not results:
        raise ValueError("at least one realtime signal result is required")
    title_versions = " / ".join(item["version"] for item in results)
    exit_summary = "；".join(f"{item['version']}={item['exit_code'] or '未记录'}" for item in results)

    md = out_dir / f"microcap_realtime_signal_digest_{date_s}.md"
    lines = [
        f"# 微盘股 {title_versions} 实时信号日报 - {date_s}",
        "",
        f"> 用途：这是自动化仓库中的微盘股 {title_versions} 实时信号推送，直接使用盘中/实时 quote 输出当日信号。",
        "",
        "## 调度审计",
        "",
        f"- 计划时间：{args.planned}",
        f"- 实际启动：{started or '未记录'}",
        f"- 完成时间：{finished}",
        f"- Workflow Run：{run_url or '未提供'}",
        f"- 脚本退出码：{exit_summary}",
        "",
        "---",
        "",
        "## 信号摘要",
        "",
    ]
    for item in results:
        lines += [
            f"### {item['version']}",
            "",
            f"- 退出码：{item['exit_code'] or '未记录'}",
            f"- 原始输出：`{item['path']}`",
            "",
            item["summary"],
            "",
        ]
    lines += ["---", "", "## 原始实时信号输出", ""]
    for item in results:
        lines += [
            f"### {item['version']}",
            "",
            "```text",
            item["output"],
            "```",
            "",
        ]
    digest_text = "\n".join(lines)
    md.write_text(digest_text, encoding="utf-8")

    meta = {
        "subject": f"微盘股 {title_versions} 实时信号日报 - {date_s}",
        "body": digest_text,
        "attachment": None,
    }
    (out_dir / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
