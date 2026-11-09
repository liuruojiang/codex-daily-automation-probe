"""Recover a delivered weekly baseline from 90-day Actions artifacts, not cache."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import zipfile


WORKFLOW = "equity-momentum-boundary-weekly.yml"
MONITOR = "Weekly Equity Momentum Boundary Monitor"
BEIJING = timezone(timedelta(hours=8))


def api(endpoint, binary=False):
    result = subprocess.run(["gh", "api", endpoint], capture_output=True, check=True, timeout=60)
    return result.stdout if binary else json.loads(result.stdout)


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timezone missing")
    return result


def verified_report(payload, now, method_version):
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = ("report.json", "metadata.json", "report.txt", "report.html", "delivery-receipt.json")
        if any(archive.namelist().count(name) != 1 for name in names):
            raise ValueError("missing or duplicate report/receipt files")
        if sum(archive.getinfo(name).file_size for name in names) > 10_000_000:
            raise ValueError("report archive too large")
        files = {name: archive.read(name) for name in names}
    report, meta, receipt = (json.loads(files[name]) for name in ("report.json", "metadata.json", "delivery-receipt.json"))
    if not all(isinstance(value, dict) for value in (report, meta, receipt)):
        raise ValueError("invalid archive objects")
    if not all(isinstance(meta.get(key), str) for key in ("subject", "body", "html_body")):
        raise ValueError("invalid delivered content")
    if receipt.get("status") != "smtp_accepted" or not re.fullmatch(r"<[^<>\s]+@[^<>\s]+>", receipt.get("message_id", "")):
        raise ValueError("no SMTP acceptance evidence")
    for field, data in (("metadata_sha256", files["metadata.json"]),
                        ("body_sha256", meta["body"].encode("utf-8")),
                        ("subject_sha256", meta["subject"].encode("utf-8"))):
        if receipt.get(field) != hashlib.sha256(data).hexdigest():
            raise ValueError("SMTP receipt hash mismatch")
    if meta["body"] != files["report.txt"].decode("utf-8") or meta["html_body"] != files["report.html"].decode("utf-8"):
        raise ValueError("delivered content mismatch")
    generated = timestamp(report["generated_at_utc"])
    accepted = timestamp(receipt["accepted_at"])
    age = (now - generated).total_seconds() / 86400
    week_start = (now.astimezone(BEIJING) - timedelta(days=now.astimezone(BEIJING).weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    if not (0 < age <= 14 and generated < week_start and generated <= accepted <= now):
        raise ValueError("not a delivered prior-week report within 14 days")
    if report.get("monitor") != MONITOR or report.get("method_version") != method_version:
        raise ValueError("wrong monitor or calculation method")
    if report["generated_at_utc"] not in meta["body"]:
        raise ValueError("report timestamp not in delivered body")
    if not isinstance(report.get("core"), dict) or not isinstance(report.get("supplemental"), dict):
        raise ValueError("invalid report data")
    if (not all(isinstance(value, dict) for value in report["core"].values())
            or not isinstance(report.get("sources"), list)
            or not all(isinstance(source, dict) for source in report["sources"])):
        raise ValueError("invalid report components or sources")
    return report, files["report.json"]


def restore(repository, output_dir, now, method_version):
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "report.json"
    if target.exists():
        raise FileExistsError("Refusing to reuse an existing baseline; use a fresh state directory")
    result = {"status": "missing", "reason": "近14天内没有找到带发送凭据且口径一致的上周报告；未使用临时缓存。", "attempts": []}
    try:
        runs = api(f"repos/{repository}/actions/workflows/{WORKFLOW}/runs?branch=main&status=success&per_page=100")["workflow_runs"]
        for run in runs:
            created = timestamp(run["created_at"])
            if created > now or (now - created).total_seconds() > 15 * 86400:
                continue
            if run.get("head_branch") != "main" or run.get("event") not in ("schedule", "push", "workflow_dispatch"):
                continue
            if run.get("path") != ".github/workflows/" + WORKFLOW:
                continue
            artifacts = api(f"repos/{repository}/actions/runs/{run['id']}/artifacts?per_page=100")["artifacts"]
            for artifact in artifacts:
                if artifact.get("expired") or artifact["name"] != f"equity-momentum-boundary-{run['id']}":
                    continue
                try:
                    report, raw = verified_report(api(f"repos/{repository}/actions/artifacts/{artifact['id']}/zip", binary=True), now, method_version)
                    if abs((timestamp(report["generated_at_utc"]) - created).total_seconds()) > 86400:
                        raise ValueError("report timestamp does not match workflow run")
                except (ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
                    result["attempts"].append({"run_id": run["id"], "reason": str(error)})
                    continue
                target.write_bytes(raw)
                result.update(status="restored", reason="已从持久归档恢复上周已发送报告，发送凭据及正文哈希核对通过。",
                              run_id=run["id"], run_url=run["html_url"], artifact_id=artifact["id"],
                              generated_at_utc=report["generated_at_utc"], report_sha256=hashlib.sha256(raw).hexdigest())
                break
            if result["status"] == "restored":
                break
    except (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError) as error:
        # Do not expose gh stderr, tokens, or signed download URLs in the email.
        result.update(status="unavailable", reason="上期归档读取失败（权限、网络或归档接口异常）；本期仅报告可独立核实的数据。", error_type=type(error).__name__)
    (output_dir / "restore.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    from monitor import METHOD_VERSION
    result = restore(args.repository, args.output_dir, datetime.now(timezone.utc), METHOD_VERSION)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
