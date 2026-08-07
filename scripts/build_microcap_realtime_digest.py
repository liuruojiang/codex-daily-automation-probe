from __future__ import annotations

import argparse
import csv
import json
import os
import re
from datetime import date, datetime, timedelta
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


def extract_value(text: str, key: str) -> str:
    line = extract_line(text, rf"^{re.escape(key)}\s*:[^\n]*")
    if ":" not in line:
        return ""
    return line.split(":", 1)[1].strip()


def parse_iso_date(value: str) -> date | None:
    match = re.search(r"\d{4}-\d{2}-\d{2}", value.strip())
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(0))
    except ValueError:
        return None


def previous_weekday(value: date) -> date:
    value = value - timedelta(days=1)
    while value.weekday() >= 5:
        value = value - timedelta(days=1)
    return value


def classify_signal_output(output: str, exit_code: str) -> tuple[str, str]:
    code = exit_code.strip().lower()
    if code and code not in {"0", "none", "unknown"}:
        return "FAILED", f"script exit code is {exit_code}"
    if "preflight_failed" in output:
        reason = extract_value(output, "reason")
        if reason:
            return "FAILED", reason
        refresh_code = extract_value(output, "refresh_exit_code")
        if refresh_code:
            return "FAILED", f"state refresh failed with exit code {refresh_code}"
        return "FAILED", "state refresh failed before realtime signal ran"
    if "realtime_signal" not in output:
        return "FAILED", "realtime_signal marker is missing"

    anchor = parse_iso_date(extract_value(output, "latest_anchor_trade_date"))
    quote_trade_date = parse_iso_date(extract_value(output, "quote_trade_date"))
    if anchor and quote_trade_date and anchor < quote_trade_date:
        expected_anchor = previous_weekday(quote_trade_date)
        if anchor < expected_anchor:
            return "STALE", f"anchor {anchor.isoformat()} is older than expected {expected_anchor.isoformat()}"
    return "OK", ""


def worst_status(statuses: list[str]) -> str:
    if any(status == "FAILED" for status in statuses):
        return "FAILED"
    if any(status == "STALE" for status in statuses):
        return "STALE"
    return "OK"


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
        "microcap_mom",
        "hedge_mom",
        "momentum_gap",
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


def parse_output_fields(output: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for match in re.finditer(r"^(?P<key>[A-Za-z0-9_]+)\s*:\s*(?P<value>[^\n]*)$", output, flags=re.M):
        fields[match.group("key")] = match.group("value").strip()
    return fields


def read_last_csv_row(path_value: str) -> dict[str, str]:
    if not path_value:
        return {}
    path = Path(path_value)
    if not path.exists() or not path.is_file():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return {}
    return {str(key): str(value).strip() for key, value in rows[-1].items() if key and value is not None}


def parse_signal_csv_specs(specs: list[str]) -> dict[str, str]:
    mapped: dict[str, str] = {}
    for spec in specs:
        version, value = split_version_spec(spec, "")
        if not version:
            raise ValueError("--signal-csv values must use version=path format")
        mapped[version] = value
    return mapped


def first_value(fields: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = fields.get(key, "").strip()
        if value and value.lower() not in {"nan", "none"}:
            return value
    return ""


def parse_number(value: str) -> float | None:
    cleaned = value.strip().replace(",", "")
    if not cleaned or cleaned.lower() in {"nan", "none"}:
        return None
    is_percent = cleaned.endswith("%")
    if is_percent:
        cleaned = cleaned[:-1]
    try:
        number = float(cleaned)
    except ValueError:
        return None
    return number / 100.0 if is_percent else number


def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def format_percent(value: str, *, signed: bool = True) -> str:
    number = parse_number(value)
    if number is None:
        return "N/A"
    return f"{number:+.2%}" if signed else f"{number:.2%}"


def format_r2(value: str) -> str:
    number = parse_number(value)
    return "N/A" if number is None else f"{number:.3f}"


def format_scale(fields: dict[str, str]) -> str:
    value = first_value(
        fields,
        "next_session_actionable_scale",
        "target_vol_next_execution_scale",
        "next_session_target_scale",
        "target_position_scale",
        "current_execution_scale",
        "execution_scale",
    )
    number = parse_number(value)
    return "N/A" if number is None else f"{number:.2f}"


HOLDING_LABELS = {
    "cash": "空仓",
    "long_microcap_short_zz1000": "微盘 Top100＋空头中证1000",
    "long_microcap_top100": "微盘 Top100",
}


def format_holding(value: str) -> str:
    normalized = value.strip()
    return HOLDING_LABELS.get(normalized, normalized or "未知")


def member_rebalance_action(fields: dict[str, str]) -> str:
    state = first_value(fields, "member_rebalance_state").lower()
    required = parse_bool(first_value(fields, "member_rebalance_required"))
    if not required and state in {"", "hold", "none", "no_rebalance"}:
        return ""

    label = first_value(fields, "member_rebalance_label")
    if label:
        return label
    enter_count = first_value(fields, "member_enter_count") or "0"
    exit_count = first_value(fields, "member_exit_count") or "0"
    return f"名单调仓（调入 {enter_count}，调出 {exit_count}）"


def action_label(item: dict[str, object]) -> str:
    if item["status"] != "OK":
        return "异常"
    fields = item["fields"]
    assert isinstance(fields, dict)
    current = first_value(fields, "current_holding", "holding")
    next_holding = first_value(fields, "next_holding")
    if current and next_holding and current != next_holding:
        if current == "cash" and next_holding != "cash":
            return "开仓"
        if current != "cash" and next_holding == "cash":
            return "平仓"
        return "调仓"

    holding_state = first_value(fields, "holding_trade_state").lower()
    trade_state = first_value(fields, "trade_state").lower()
    if holding_state not in {"", "hold"} or trade_state not in {"", "hold"}:
        state = holding_state if holding_state not in {"", "hold"} else trade_state
        if state in {"enter", "entry", "open", "buy"}:
            return "开仓"
        if state in {"exit", "close", "sell"}:
            return "平仓"
        return "调仓"

    actions: list[str] = []
    scale_state = first_value(fields, "scale_trade_state").lower()
    if parse_bool(first_value(fields, "scale_trade_required")) or scale_state not in {"", "hold_scale"}:
        actions.append("调整仓位")
    member_action = member_rebalance_action(fields)
    if member_action:
        actions.append(member_action)
    return "；".join(actions) or "无操作"


def momentum_text(version: str, fields: dict[str, str]) -> str:
    if version == "v2.0":
        values = [
            ("微盘", first_value(fields, "microcap_mom")),
            ("对冲", first_value(fields, "hedge_mom")),
            ("动量差", first_value(fields, "momentum_gap")),
        ]
        rendered = [f"{label} **{format_percent(value)}**" for label, value in values if value]
        return "；".join(rendered) or "N/A"

    score = first_value(fields, "annualized_log_wls_score", "momentum_gap")
    r2 = first_value(fields, "log_wls_r2")
    if version == "v2.3":
        label = "对冲价差年化 WLS 得分"
    elif version == "v2.5":
        label = "微盘年化 WLS 得分"
    else:
        label = "年化 WLS 得分"
    parts = []
    if score:
        parts.append(f"{label} **{format_percent(score)}**")
    if r2:
        parts.append(f"R² **{format_r2(r2)}**")
    return "；".join(parts) or "N/A"


def humanize_status_note(status: str, note: str) -> str:
    if status == "STALE":
        match = re.search(r"anchor (\d{4}-\d{2}-\d{2}) is older than expected (\d{4}-\d{2}-\d{2})", note)
        if match:
            return f"数据过期，锚点 {match.group(1)} 早于应有日期 {match.group(2)}"
        return f"数据过期：{note}"
    if "state refresh failed" in note.lower():
        return "状态刷新失败，实时信号未运行"
    match = re.search(r"script exit code is ([^\s]+)", note)
    if match:
        return f"脚本运行失败，退出码 {match.group(1)}"
    if "marker is missing" in note.lower():
        return "未生成实时信号标记"
    if note.startswith("required signal fields are missing:"):
        fields = note.split(":", 1)[1].strip()
        return f"缺少必要信号字段：{fields}"
    return f"运行失败：{note}" if note else "运行失败"


def risk_warnings(item: dict[str, object]) -> list[str]:
    version = str(item["version"])
    status = str(item["status"])
    note = str(item["status_note"])
    fields = item["fields"]
    assert isinstance(fields, dict)
    if status != "OK":
        return [f"{version}：{humanize_status_note(status, note)}"]

    warnings: list[str] = []
    fallback_warning = first_value(fields, "fallback_warning")
    if fallback_warning:
        warnings.append(f"{version}：行情使用回退数据（{fallback_warning}）")

    if version == "v2.0" and parse_bool(first_value(fields, "blocked_until_signal_reset")):
        metric = first_value(fields, "overheat_metric")
        threshold = first_value(fields, "overheat_threshold")
        detail = "过热退出后锁定，等待基础信号重置"
        if metric and threshold:
            detail += f"；当前指标 {format_percent(metric, signed=False)}，触发线 {format_percent(threshold, signed=False)}"
        warnings.append(f"v2.0：{detail}")

    if version == "v2.3" and parse_bool(first_value(fields, "overheat_risk_off")):
        value = first_value(fields, "overheat_feature_value")
        trigger = first_value(fields, "overheat_trigger_threshold")
        recovery = first_value(fields, "overheat_recovery_threshold")
        detail = "过热风险关闭中"
        if value and trigger:
            detail += f"；当前指标 {format_percent(value, signed=False)}，触发线 {format_percent(trigger, signed=False)}"
        if recovery:
            detail += f"，恢复线 {format_percent(recovery, signed=False)}"
        warnings.append(f"v2.3：{detail}")
    return warnings


def subject_tag(results: list[dict[str, object]]) -> str:
    if any(item["status"] != "OK" for item in results):
        return "异常"
    if any(action_label(item) != "无操作" for item in results):
        return "需操作"
    return "无需操作"


def conclusion_text(results: list[dict[str, object]]) -> str:
    if any(item["status"] != "OK" for item in results):
        return "存在异常版本，请勿执行异常版本信号。"
    actionable = [(str(item["version"]), action_label(item)) for item in results if action_label(item) != "无操作"]
    if not actionable:
        return "所有版本均无需调仓。"
    phrases = [f"{version} 需要{action}" for version, action in actionable]
    if len(actionable) < len(results):
        return "；".join(phrases) + "；其他版本无需调仓。"
    return "；".join(phrases) + "。"


def shared_data_line(results: list[dict[str, object]]) -> str:
    fields_list = [item["fields"] for item in results]
    snapshots = [first_value(fields, "snapshot_time") for fields in fields_list if isinstance(fields, dict)]
    snapshots = [value for value in snapshots if value]
    quote_dates = [first_value(fields, "quote_trade_date") for fields in fields_list if isinstance(fields, dict)]
    quote_dates = [value for value in quote_dates if value]
    coverages = [first_value(fields, "quote_coverage") for fields in fields_list if isinstance(fields, dict)]
    coverages = [value for value in coverages if value]

    if snapshots:
        data_value = max(snapshots)
    elif quote_dates:
        data_value = max(quote_dates)
    else:
        data_value = "未记录"
    line = f"数据时间：{data_value}"
    if coverages and len(set(coverages)) == 1:
        line += f"｜报价覆盖：{coverages[0]}"
    return line


def escape_table_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def build_compact_digest(
    results: list[dict[str, object]],
    *,
    date_s: str,
    run_url: str,
    subject_prefix: str = "",
) -> tuple[str, str]:
    versions = "/".join(str(item["version"]) for item in results)
    tag = subject_tag(results)
    lines = [
        "## 今日结论",
        "",
        f"**{conclusion_text(results)}**",
        "",
        "| 版本 | 当前 → 下一持仓 | 今日操作 | 下一交易日仓位 | 核心动量 |",
        "|---|---|---|---:|---|",
    ]
    for item in results:
        fields = item["fields"]
        assert isinstance(fields, dict)
        current = format_holding(first_value(fields, "current_holding", "holding"))
        next_holding = format_holding(first_value(fields, "next_holding"))
        lines.append(
            "| "
            + " | ".join(
                [
                    escape_table_cell(str(item["version"])),
                    escape_table_cell(f"{current} → {next_holding}"),
                    escape_table_cell(action_label(item)),
                    escape_table_cell(format_scale(fields)),
                    escape_table_cell(momentum_text(str(item["version"]), fields)),
                ]
            )
            + " |"
        )

    warnings = [warning for item in results for warning in risk_warnings(item)]
    lines += ["", "## 需要关注", ""]
    if warnings:
        lines += [f"- **{warning.split('：', 1)[0]}：**{warning.split('：', 1)[1]}" for warning in warnings]
    else:
        lines.append("风险/数据异常：无")
    lines += ["", shared_data_line(results)]
    if run_url:
        lines += [f"[查看完整诊断与原始输出]({run_url})"]
    body = "\n".join(lines)
    normalized_prefix = subject_prefix.strip().strip("[]")
    prefix = f"[{normalized_prefix}]" if normalized_prefix else ""
    subject = f"{prefix}[{tag}] 微盘股 {versions} 日报 - {date_s}"
    return subject, body


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
    parser.add_argument("--subject-prefix", default="")
    parser.add_argument("--exit-code", action="append", default=[], help="Exit code, or version=code. Can be repeated.")
    parser.add_argument(
        "--signal-csv",
        action="append",
        default=[],
        help="Optional version=path realtime signal CSV. Can be repeated.",
    )
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    exit_codes, default_exit_code = parse_exit_codes(args.exit_code)
    signal_csv_paths = parse_signal_csv_specs(args.signal_csv)
    result_specs = [split_version_spec(spec, "v2.0" if len(args.result) == 1 else "") for spec in args.result]
    results: list[dict[str, object]] = []
    for version, result_value in result_specs:
        if not version:
            raise ValueError("multiple --result values must use version=path format")
        result_path = Path(result_value)
        raw = result_path.read_text(encoding="utf-8", errors="replace") if result_path.exists() else "未找到实时信号输出文件。"
        output = clean_output(raw)
        exit_code = exit_codes.get(version, default_exit_code)
        status, status_note = classify_signal_output(output, exit_code)
        fields = parse_output_fields(output)
        fields.update(read_last_csv_row(signal_csv_paths.get(version, "")))
        if status == "OK":
            missing = [key for key in ("current_holding", "next_holding") if not first_value(fields, key)]
            if missing:
                status = "FAILED"
                status_note = "required signal fields are missing: " + ", ".join(missing)
        results.append(
            {
                "version": version,
                "path": str(result_path),
                "output": output,
                "fields": fields,
                "exit_code": exit_code,
                "status": status,
                "status_note": status_note,
            }
        )

    if not results:
        raise ValueError("at least one realtime signal result is required")

    date_s = now_bj().date().isoformat()
    run_url = os.environ.get("GITHUB_RUN_URL", "")
    subject, digest_text = build_compact_digest(
        results,
        date_s=date_s,
        run_url=run_url,
        subject_prefix=args.subject_prefix,
    )

    md = out_dir / f"microcap_realtime_signal_digest_{date_s}.md"
    md.write_text(digest_text, encoding="utf-8")

    meta = {
        "subject": subject,
        "body": digest_text,
        "attachment": None,
    }
    (out_dir / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
