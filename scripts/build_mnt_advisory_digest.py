from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


BJ = ZoneInfo("Asia/Shanghai")
OWNER = "liuruojiang"
REPO = "MNT"
WORKFLOW = "v76_level8_advisory_outputs.yml"
SUMMARY_PATH = "outputs/portfolio_v76_current/level8_action_summary.md"


def request_text(url: str, *, accept: str = "application/vnd.github+json") -> str:
    headers = {
        "Accept": accept,
        "User-Agent": "codex-daily-automation-probe",
    }
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GET {url} failed: HTTP {exc.code}: {body[:500]}") from exc


def latest_workflow_run() -> dict[str, object]:
    url = (
        f"https://api.github.com/repos/{OWNER}/{REPO}/actions/workflows/"
        f"{WORKFLOW}/runs?branch=main&per_page=5"
    )
    payload = json.loads(request_text(url))
    runs = payload.get("workflow_runs") or []
    if not runs:
        raise RuntimeError(f"No workflow runs found for {OWNER}/{REPO} {WORKFLOW}")
    return runs[0]


def current_action_summary(summary_path: str = "") -> str:
    if summary_path:
        path = Path(summary_path)
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace").strip()
        return f"MNT advisory summary was not generated: missing {summary_path}"
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{SUMMARY_PATH}?ref=main"
    return request_text(url, accept="application/vnd.github.raw+json").strip()


def compact_markdown(text: str, max_len: int = 12000) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    if len(text) <= max_len:
        return text
    return text[: max_len - 80].rstrip() + "\n\n[truncated; see the GitHub output file for full text]"


def repair_common_mojibake(text: str) -> str:
    markers = ("浠婃棩", "鎵ц", "鐘舵", "鏁版嵁", "琚栫")
    if not any(marker in text for marker in markers):
        return text
    try:
        repaired = text.encode("gbk").decode("utf-8")
    except UnicodeError:
        return text
    return repaired if "今日" in repaired or "执行" in repaired else text


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a Gmail-ready MNT V7.6 advisory digest.")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--planned", default="13:05 Asia/Shanghai")
    parser.add_argument("--summary-path", default="")
    parser.add_argument("--run-url", default="")
    parser.add_argument("--microcap-exit-code", default="")
    parser.add_argument("--source-returns-exit-code", default="")
    parser.add_argument("--build-exit-code", default="")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(BJ)
    date_s = now.date().isoformat()
    if args.summary_path:
        run = {
            "html_url": args.run_url,
            "conclusion": "success"
            if all(code == "0" for code in (args.microcap_exit_code, args.source_returns_exit_code, args.build_exit_code))
            else "failure",
            "event": os.environ.get("GITHUB_EVENT_NAME", "workflow"),
            "created_at": os.environ.get("STARTED_BJ", ""),
            "updated_at": now.isoformat(),
            "head_sha": os.environ.get("GITHUB_SHA", ""),
            "id": os.environ.get("GITHUB_RUN_ID", ""),
        }
    else:
        run = latest_workflow_run()
    summary = compact_markdown(repair_common_mojibake(current_action_summary(args.summary_path)))

    run_url = str(run.get("html_url") or "")
    conclusion = str(run.get("conclusion") or run.get("status") or "unknown")
    event = str(run.get("event") or "")
    created_at = str(run.get("created_at") or "")
    updated_at = str(run.get("updated_at") or "")
    head_sha = str(run.get("head_sha") or "")[:12]
    run_id = str(run.get("id") or "")

    attachment = out_dir / f"mnt_v76_advisory_digest_{date_s}.md"
    attachment.write_text(
        "\n".join(
            [
                f"# MNT V7.6 Level-8 Advisory Digest - {date_s}",
                "",
                "## Latest GitHub Actions run",
                "",
                f"- Repository: {OWNER}/{REPO}",
                f"- Workflow: {WORKFLOW}",
                f"- Latest run: {run_id}",
                f"- Conclusion: {conclusion}",
                f"- Event: {event}",
                f"- Head SHA: {head_sha}",
                f"- Created UTC: {created_at}",
                f"- Updated UTC: {updated_at}",
                f"- Run URL: {run_url}",
                f"- Planned digest time: {args.planned}",
                f"- Digest generated: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}",
                f"- Microcap v2.0 source exit code: {args.microcap_exit_code or 'n/a'}",
                f"- Five-sleeve returns exit code: {args.source_returns_exit_code or 'n/a'}",
                f"- Advisory build exit code: {args.build_exit_code or 'n/a'}",
                "",
                "## Action summary",
                "",
                summary,
                "",
                "## Source file",
                "",
                f"https://github.com/{OWNER}/{REPO}/blob/main/{SUMMARY_PATH}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    body = "\n".join(
        [
            f"MNT V7.6 Advisory digest generated for {date_s}.",
            "",
            f"Latest run: {run_id}",
            f"Conclusion: {conclusion}",
            f"Event: {event}",
            f"Run URL: {run_url}",
            f"Microcap v2.0 source exit code: {args.microcap_exit_code or 'n/a'}",
            f"Five-sleeve returns exit code: {args.source_returns_exit_code or 'n/a'}",
            f"Advisory build exit code: {args.build_exit_code or 'n/a'}",
            "",
            "Action summary:",
            summary[:3500],
            "",
            f"Full digest is attached. Source file: https://github.com/{OWNER}/{REPO}/blob/main/{SUMMARY_PATH}",
        ]
    )
    meta = {
        "subject": f"MNT V7.6 Advisory - {conclusion} - {date_s}",
        "body": body,
        "attachment": str(attachment),
    }
    (out_dir / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
