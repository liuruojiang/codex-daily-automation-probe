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
import zlib


WORKFLOW = "equity-momentum-boundary-weekly.yml"
MONITOR = "Weekly Equity Momentum Boundary Monitor"
BEIJING = timezone(timedelta(hours=8))
MAX_RUN_PAGES = 3
MAX_ARTIFACT_PAGES = 3


class RecoveryUnavailable(RuntimeError):
    """Stop a repository-wide auth/rate-limit failure without exposing stderr."""

    def __init__(self, status_code):
        super().__init__("repository authorization, rate limit, or gh availability")
        self.status_code = status_code


def http_status(error):
    stderr = getattr(error, "stderr", b"") or b""
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    match = re.search(r"\bHTTP\s+(\d{3})\b", stderr)
    return int(match.group(1)) if match else None


def candidate_api(endpoint, result, run_id, binary=False):
    """Candidate failures are local; auth/rate-limit failures are global."""
    try:
        return api(endpoint, binary=binary)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        result["attempts"].append({"run_id": run_id, "reason": "candidate API payload invalid",
                                   "error_type": type(error).__name__})
        return None
    except (subprocess.SubprocessError, OSError) as error:
        code = http_status(error)
        if code in (401, 403, 429) or isinstance(error, FileNotFoundError):
            raise RecoveryUnavailable(code) from None
        result["attempts"].append({"run_id": run_id, "reason": "candidate API unavailable",
                                   "error_type": type(error).__name__, "http_status": code})
        return None


def candidate_artifacts(repository, run_id, result):
    """Bounded per-run pagination; incomplete searches are visible in provenance."""
    artifacts = []
    capped = False
    for page in range(1, MAX_ARTIFACT_PAGES + 1):
        response = candidate_api(f"repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100&page={page}", result, run_id)
        if response is None:
            capped = True
            break
        if not isinstance(response, dict) or not isinstance(response.get("artifacts"), list):
            result["attempts"].append({"run_id": run_id, "reason": "candidate artifact listing invalid"})
            capped = True
            break
        batch = response["artifacts"]
        artifacts.extend(batch)
        total = response.get("total_count")
        known_total = type(total) is int and total >= 0
        more = len(batch) >= 100 or (known_total and total > len(artifacts))
        if not more:
            break
        if page == MAX_ARTIFACT_PAGES:
            capped = True
    result.setdefault("artifact_search", {})[str(run_id)] = {"pages_scanned": page, "search_incomplete": capped}
    result["artifact_search_capped"] = result.get("artifact_search_capped", False) or capped
    return artifacts


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


def workflow_identity_matches(value, run):
    """Artifacts and attempt responses must point to the same branch and code."""
    return (isinstance(value, dict) and value.get("id") == run.get("id")
            and value.get("head_branch") == run.get("head_branch") == "main"
            and isinstance(run.get("head_sha"), str)
            and value.get("head_sha") == run["head_sha"])


def artifact_attempt(repository, artifact, run, latest_start, result):
    """Return this archive's attempt, never assign the latest start to old ZIPs."""
    if "workflow_run" in artifact and not workflow_identity_matches(artifact["workflow_run"], run):
        raise ValueError("artifact workflow identity mismatch")
    prefix = f"equity-momentum-boundary-{run['id']}"
    if artifact.get("name") == prefix:
        if run.get("run_attempt", 1) != 1:
            raise ValueError("legacy archive has no attempt identity for a rerun")
        return 1, latest_start
    match = re.fullmatch(re.escape(prefix) + r"-([1-9]\d*)", str(artifact.get("name", "")))
    if not match:
        return None
    attempt_number = int(match.group(1))
    if attempt_number > run.get("run_attempt", 1):
        raise ValueError("archive attempt is newer than completed run metadata")
    attempt = candidate_api(f"repos/{repository}/actions/runs/{run['id']}/attempts/{attempt_number}", result, run["id"])
    if attempt is None:
        return None
    if not workflow_identity_matches(attempt, run) or attempt.get("run_attempt") != attempt_number:
        raise ValueError("archive attempt workflow identity mismatch")
    return attempt_number, timestamp(attempt["run_started_at"])


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
    if "report_sha256" not in meta:
        raise ValueError("legacy snapshot has no report_sha256 binding")
    from delivery_contract import verify_report_binding
    verify_report_binding(meta, files["report.json"])
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
    result = {"status": "missing", "reason": "近14天内没有找到正文已绑定、带发送凭据且口径一致的上周报告；未使用临时缓存。", "attempts": []}
    try:
        # The API orders by original run creation. A late rerun can be newer
        # than a recently created run, so collect bounded pages then sort by
        # the start of the actual attempt, rather than created_at.
        runs = []
        for page in range(1, MAX_RUN_PAGES + 1):
            batch = api(f"repos/{repository}/actions/workflows/{WORKFLOW}/runs?branch=main&status=completed&per_page=100&page={page}")["workflow_runs"]
            if not isinstance(batch, list):
                raise ValueError("invalid workflow run listing")
            runs.extend(batch)
            if len(batch) < 100:
                break
        result["run_pages_scanned"] = page
        result["run_search_capped"] = len(batch) == 100 and page == MAX_RUN_PAGES
        candidates = []
        for run in runs:
            try:
                if type(run.get("id")) is not int or run["id"] <= 0:
                    raise ValueError("invalid run ID")
                # An initial attempt may omit run_started_at in old API data.
                # A rerun must provide its real attempt start, not an old
                # created_at timestamp that would mis-date a valid delivery.
                started_value = run.get("run_started_at")
                if not started_value and run.get("run_attempt", 1) == 1:
                    started_value = run["created_at"]
                started = timestamp(started_value)
            except (ValueError, KeyError, TypeError, AttributeError):
                result["attempts"].append({"run_id": run.get("id") if isinstance(run, dict) else None,
                                           "reason": "workflow run metadata invalid"})
                continue
            if started > now or (now - started).total_seconds() > 15 * 86400:
                continue
            if run.get("head_branch") != "main" or run.get("event") not in ("schedule", "push", "workflow_dispatch"):
                continue
            if run.get("path") != ".github/workflows/" + WORKFLOW:
                continue
            candidates.append((started, run))
        # An archive is identified by its own attempt. Sorting run-level start
        # times alone would let a new same-week rerun promote its older ZIP.
        archive_candidates = []
        for latest_start, run in candidates:
            for artifact in candidate_artifacts(repository, run["id"], result):
                if not isinstance(artifact, dict):
                    result["attempts"].append({"run_id": run["id"], "reason": "candidate artifact metadata invalid"})
                    continue
                if type(artifact.get("id")) is not int or artifact["id"] <= 0:
                    result["attempts"].append({"run_id": run["id"], "reason": "candidate artifact ID invalid"})
                    continue
                if artifact.get("expired"):
                    continue
                try:
                    specific_attempt = artifact_attempt(repository, artifact, run, latest_start, result)
                    if specific_attempt is None:
                        continue
                    attempt_number, started = specific_attempt
                    if started > now or (now - started).total_seconds() > 15 * 86400:
                        continue
                except (ValueError, KeyError, TypeError) as error:
                    result["attempts"].append({"run_id": run["id"], "reason": "archive attempt identity or date invalid",
                                               "error_type": type(error).__name__})
                    continue
                archive_candidates.append((started, run, artifact, attempt_number))
        archive_candidates.sort(key=lambda entry: entry[0], reverse=True)
        legacy_rejected = False
        for started, run, artifact, attempt_number in archive_candidates:
            payload = candidate_api(f"repos/{repository}/actions/artifacts/{artifact['id']}/zip", result, run["id"], binary=True)
            if payload is None:
                continue
            try:
                report, raw = verified_report(payload, now, method_version)
                since_start = (timestamp(report["generated_at_utc"]) - started).total_seconds()
                if not 0 <= since_start <= 86400:
                    raise ValueError("report timestamp does not match workflow attempt")
            except (ValueError, KeyError, TypeError, zipfile.BadZipFile, zipfile.LargeZipFile,
                    zlib.error, EOFError, RuntimeError, NotImplementedError) as error:
                legacy = isinstance(error, ValueError) and str(error) == "legacy snapshot has no report_sha256 binding"
                legacy_rejected |= legacy
                result["attempts"].append({"run_id": run["id"],
                    "reason": "legacy snapshot has no report_sha256 binding" if legacy else "invalid or damaged candidate archive",
                    "error_type": type(error).__name__})
                continue
            target.write_bytes(raw)
            result.update(status="restored", reason="已从持久归档恢复上周已发送报告，发送凭据及正文哈希核对通过。",
                          run_id=run["id"], run_url=run.get("html_url") or f"https://github.com/{repository}/actions/runs/{run['id']}", artifact_id=artifact["id"],
                          generated_at_utc=report["generated_at_utc"], report_sha256=hashlib.sha256(raw).hexdigest(),
                          binding="sha256", run_started_at=started.isoformat(), run_attempt=attempt_number)
            break
        if result["status"] == "missing" and legacy_rejected:
            result["reason"] = "旧报告缺少数据绑定，未作为比较基线；需在新绑定报告正常发送后建立可比基线。"
        if result["status"] == "missing" and result["run_search_capped"]:
            result["reason"] += " 已达最近300次运行的检索上限，未断言更早记录不存在。"
        if result["status"] == "missing" and result.get("artifact_search_capped"):
            result["reason"] += " 部分运行的归档分页读取未完成或达到检索上限，不能确认这些运行没有合格基线。"
    except (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError, RecoveryUnavailable) as error:
        # Do not expose gh stderr, tokens, or signed download URLs in the email.
        result.update(status="unavailable", reason="上期归档读取失败（权限、网络或归档接口异常）；本期仅报告可独立核实的数据。", error_type=type(error).__name__)
        result["http_status"] = error.status_code if isinstance(error, RecoveryUnavailable) else http_status(error)
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
