from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import urllib.parse
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from time import sleep
from zoneinfo import ZoneInfo

from restore_microcap_member_checkpoint import retry_checkpoint_read


BJ = ZoneInfo("Asia/Shanghai")


def delivery_json(request):
    def read():
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    return retry_checkpoint_read(read, 'delivery metadata request', pause=sleep)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def beijing_delivery_date(value: datetime) -> date:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(BJ).date()


def delivery_marker_name(value: date, publication_mode: str | None = None) -> str:
    if publication_mode is None:
        return f"microcap-realtime-digest-delivered-{value.isoformat()}"
    if publication_mode not in {"realtime", "close_confirmed"}:
        raise ValueError(f"unsupported publication mode: {publication_mode}")
    return f"microcap-v2-{publication_mode}-digest-delivered-{value.isoformat()}"


def test_delivery_marker_name(test_id: str, publication_mode: str) -> str:
    if re.fullmatch(r"[a-z0-9][a-z0-9-]{7,63}", test_id, flags=re.ASCII) is None:
        raise ValueError("delivery test ID must contain 8-64 lowercase ASCII letters, digits or hyphens")
    if publication_mode != "close_confirmed":
        raise ValueError("delivery tests require close-confirmed publication")
    # Reusing an ID on another day must still suppress the same acceptance mail.
    return f"microcap-v2-{publication_mode}-delivery-test-{test_id}"


def marker_exists(payload: dict[str, object], marker_name: str) -> bool:
    artifacts = payload.get("artifacts", [])
    if not isinstance(artifacts, list):
        return False
    return any(
        isinstance(item, dict)
        and item.get("name") == marker_name
        and item.get("expired") is not True
        for item in artifacts
    )


def should_send(*, correction: bool, marker_already_exists: bool) -> bool:
    return correction or not marker_already_exists


def legacy_marker_mode(repository: str, token: str, api_url: str, artifact: dict, day: date) -> str:
    """Old date-only markers require their own run's successful metadata as proof."""
    from restore_ic_im_v1_3_ledger import api_request, download
    run_id = (artifact.get("workflow_run") or {}).get("id")
    if not isinstance(run_id, int) or run_id <= 0:
        raise RuntimeError("BLOCKED: legacy marker has no verifiable workflow run")
    base = f"{api_url.rstrip('/')}/repos/{repository}/actions"
    payload = delivery_json(api_request(f"{base}/runs/{run_id}/artifacts?per_page=100", token))
    reports = [item for item in payload.get("artifacts", []) if isinstance(item, dict)
               and item.get("name") == "microcap-realtime-digest" and item.get("expired") is not True]
    if len(reports) != 1:
        raise RuntimeError("BLOCKED: legacy marker has no unique, nonexpired digest artifact")
    report = reports[0]
    if report.get("archive_download_url") != f"{base}/artifacts/{report.get('id')}/zip":
        raise RuntimeError("BLOCKED: unexpected legacy digest artifact URL")
    raw = retry_checkpoint_read(lambda: download(report, token), 'delivery report archive request', pause=sleep)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = [item for item in archive.infolist() if item.filename in {"artifacts/metadata.json", "metadata.json"}]
        if len(entries) != 1 or entries[0].file_size > 1024 * 1024:
            raise RuntimeError("BLOCKED: legacy digest metadata missing, ambiguous or too large")
        meta = json.loads(archive.read(entries[0]).decode("utf-8"))
    if (not isinstance(meta, dict) or meta.get("status") != "OK"
            or meta.get("signal_date") != day.isoformat()
            or meta.get("publication_mode") not in {"realtime", "close_confirmed"}):
        raise RuntimeError("BLOCKED: legacy digest date/status/mode cannot be verified")
    return meta["publication_mode"]


def fetch_artifacts(
    repository: str,
    token: str,
    marker_name: str,
    api_url: str = "https://api.github.com",
) -> dict[str, object]:
    quoted_name = urllib.parse.quote(marker_name, safe="")
    url = f"{api_url.rstrip('/')}/repos/{repository}/actions/artifacts?name={quoted_name}&per_page=100"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "microcap-delivery-gate",
        },
    )
    payload = delivery_json(request)
    if not isinstance(payload, dict) or not isinstance(payload.get("artifacts"), list):
        raise RuntimeError("GitHub artifacts response must contain an artifacts list")
    return payload


def verify_smtp_receipt(repository: str, token: str, api_url: str, artifact: dict,
                        day: date, publication_mode: str, test_id: str = "") -> None:
    """A matching artifact name alone cannot prove that SMTP accepted a digest."""
    from restore_ic_im_v1_3_ledger import api_request, download

    base = f"{api_url.rstrip('/')}/repos/{repository}/actions"
    run_id = (artifact.get("workflow_run") or {}).get("id")
    artifact_id = artifact.get("id")
    if (not isinstance(run_id, int) or run_id <= 0 or not isinstance(artifact_id, int)
            or artifact_id <= 0 or artifact.get("archive_download_url") != f"{base}/artifacts/{artifact_id}/zip"):
        raise RuntimeError("BLOCKED: SMTP receipt has no verifiable workflow artifact")
    run = delivery_json(api_request(f"{base}/runs/{run_id}", token))
    workflow_path = run.get("path")
    if workflow_path not in {".github/workflows/microcap-realtime-digest.yml", ".github/workflows/microcap-member-daily.yml"}:
        raise RuntimeError("BLOCKED: SMTP receipt belongs to an unexpected workflow")
    jobs = delivery_json(api_request(f"{base}/runs/{run_id}/jobs?per_page=100", token))
    send_jobs = [job for job in jobs.get("jobs", []) if job.get("name") == "send"]
    if len(send_jobs) != 1:
        raise RuntimeError("BLOCKED: SMTP receipt has no unique send job")
    steps = {step.get("name"): step.get("conclusion") for step in send_jobs[0].get("steps", [])}
    if any(steps.get(name) != "success" for name in ("Send Gmail", "Preserve accepted SMTP receipt")):
        raise RuntimeError("BLOCKED: workflow did not confirm SMTP acceptance and receipt upload")
    raw = retry_checkpoint_read(lambda: download(artifact, token), 'SMTP receipt archive request', pause=sleep)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        values = {}
        for basename, limit in (("metadata.json", 1024 * 1024), ("smtp-accepted.json", 64 * 1024)):
            matches = [entry for entry in archive.infolist()
                       if entry.filename in {basename, f"artifacts/{basename}"}]
            if len(matches) != 1 or matches[0].file_size > limit:
                raise RuntimeError("BLOCKED: SMTP receipt content is missing, ambiguous or too large")
            raw = archive.read(matches[0])
            values[basename] = (raw, json.loads(raw.decode("utf-8")))
    metadata_raw, metadata = values["metadata.json"]
    _, receipt = values["smtp-accepted.json"]
    if not isinstance(metadata, dict) or not isinstance(receipt, dict):
        raise RuntimeError("BLOCKED: invalid SMTP receipt document")
    member_report = (workflow_path == ".github/workflows/microcap-member-daily.yml"
                     and metadata.get("schema") == "member_daily_report_v1"
                     and metadata.get("status") in {"OK", "PARTIAL"} and not test_id)
    if ((metadata.get("status") != "OK" and not member_report) or metadata.get("publication_mode") != publication_mode
            or metadata.get("delivery_test_id", "") != test_id):
        raise RuntimeError("BLOCKED: SMTP receipt metadata identity does not match delivery")
    try:
        signal_day = date.fromisoformat(metadata.get("signal_date", ""))
        accepted_at = datetime.fromisoformat(receipt.get("accepted_at", ""))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("BLOCKED: invalid SMTP receipt dates") from exc
    if (signal_day > day or (not test_id and signal_day != day) or accepted_at.tzinfo is None
            or signal_day.isoformat() != metadata.get("signal_date")
            or beijing_delivery_date(accepted_at) < signal_day
            or accepted_at > now_utc()):
        raise RuntimeError("BLOCKED: SMTP receipt dates do not match delivery")
    if not test_id and beijing_delivery_date(accepted_at) != day:
        # A long close job may cross midnight. Require GitHub's own bounded
        # run/job/send-step interval, not dates asserted by mail metadata.
        try:
            def timestamp(value):
                stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    raise ValueError("naive timestamp")
                return stamp
            job = send_jobs[0]
            send_steps = [step for step in job.get("steps", []) if step.get("name") == "Send Gmail"]
            if len(send_steps) != 1:
                raise ValueError("ambiguous send step")
            run_start = timestamp(run["run_started_at"])
            job_start = timestamp(job["started_at"])
            job_end = timestamp(job["completed_at"])
            send_start = timestamp(send_steps[0]["started_at"])
            send_end = timestamp(send_steps[0]["completed_at"])
            original_close = beijing_delivery_date(run_start) == beijing_delivery_date(job_start) == day
            manual_catch_up = (metadata.get("catch_up_date") == day.isoformat()
                and run.get("event") == "workflow_dispatch"
                and steps.get("Resolve independent completed session") == "success"
                and day < beijing_delivery_date(run_start)
                and beijing_delivery_date(run_start) == beijing_delivery_date(job_start) == beijing_delivery_date(accepted_at))
            if (publication_mode != "close_confirmed"
                    or not (original_close or manual_catch_up)
                    or not run_start <= job_start <= send_start <= accepted_at <= send_end <= job_end <= now_utc()
                    or job_end - job_start > timedelta(minutes=180)):
                raise ValueError("outside original close job")
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise RuntimeError("BLOCKED: cross-midnight receipt lacks bounded GitHub send evidence") from exc
    def valid_address(value: object) -> bool:
        return (isinstance(value, str) and value.count("@") == 1
                and all(value.split("@")) and not any(char.isspace() for char in value)
                and not any(char in value for char in "<>,;"))

    recipients = receipt.get("recipients")
    if (receipt.get("status") != "smtp_accepted"
            or type(receipt.get("schema_version")) is not int or receipt.get("schema_version") != 1
            or re.fullmatch(r"<[^\s<>]+@[^\s<>]+>", str(receipt.get("message_id", ""))) is None
            or receipt.get("metadata_sha256") != hashlib.sha256(metadata_raw).hexdigest()
            or receipt.get("subject_sha256") != hashlib.sha256(str(metadata.get("subject", "")).encode("utf-8")).hexdigest()
            or receipt.get("body_sha256") != hashlib.sha256(str(metadata.get("body", "")).encode("utf-8")).hexdigest()
            or not valid_address(receipt.get("sender")) or not isinstance(recipients, list)
            or not recipients or not all(valid_address(item) for item in recipients)):
        raise RuntimeError("BLOCKED: SMTP receipt lacks a matching acceptance record")


def write_outputs(values: dict[str, str]) -> None:
    rendered = "".join(f"{key}={value}\n" for key, value in values.items())
    output_path = os.environ.get("GITHUB_OUTPUT", "").strip()
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as handle:
            handle.write(rendered)
    else:
        print(rendered, end="")


def pending_previous_close(repository: str, token: str, api_url: str, now: datetime) -> date | None:
    """Before today's close, reconcile only yesterday's unfinished send.

    Merely having an old intent never authorizes another SMTP call. Full
    receipt/run validation remains in the ordinary gate after selecting it.
    """
    local = now.astimezone(BJ)
    if local.hour >= 15:
        return None
    previous = local.date() - timedelta(days=1)
    marker = delivery_marker_name(previous, "close_confirmed")
    if marker_exists(fetch_artifacts(repository, token, marker, api_url), marker):
        return None
    receipt = marker + "-smtp-accepted"
    if marker_exists(fetch_artifacts(repository, token, receipt, api_url), receipt):
        return previous
    intent_names = [marker + "-send-intent"]
    if previous == date(2026, 11, 2):
        intent_names.append("microcap-close-recovery-20261102-send-intent")
    for name in intent_names:
        if marker_exists(fetch_artifacts(repository, token, name, api_url), name):
            raise RuntimeError("BLOCKED: previous close SMTP intent has no completion marker; reconcile Gmail before explicit correction")
    return None


def validate_catch_up_date(value: str, now: datetime, *, publication_mode: str,
                           correction: bool, validation_only: bool, test_id: str) -> date:
    target = date.fromisoformat(value)
    if (target.isoformat() != value or target >= beijing_delivery_date(now)
            or publication_mode != "close_confirmed" or correction or validation_only or test_id
            or os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or os.environ.get("EXTERNAL_SCHEDULE") == "true"):
        raise ValueError("Catch-up requires an explicit manual prior-date normal close delivery")
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catch-up-date", default="")
    parser.add_argument("--correction", action="store_true")
    parser.add_argument("--validation-only", action="store_true")
    parser.add_argument("--delivery-test-id", default="")
    parser.add_argument("--publication-mode", choices=("realtime", "close_confirmed"), default="realtime")
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    parser.add_argument("--api-url", default=os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    args = parser.parse_args()

    if args.delivery_test_id and (args.correction or args.validation_only):
        parser.error("delivery test cannot be combined with correction or validation-only")

    now = now_utc()
    delivery_date = beijing_delivery_date(now)
    if args.catch_up_date:
        delivery_date = validate_catch_up_date(args.catch_up_date, now,
            publication_mode=args.publication_mode, correction=args.correction,
            validation_only=args.validation_only, test_id=args.delivery_test_id)
    if (not args.catch_up_date and not args.correction and not args.validation_only and not args.delivery_test_id
            and args.publication_mode == "close_confirmed"):
        if not args.repository or not args.token:
            raise SystemExit("GITHUB_REPOSITORY and GITHUB_TOKEN are required for scheduled delivery checks")
        delivery_date = pending_previous_close(args.repository, args.token, args.api_url, now) or delivery_date
    marker_name = (test_delivery_marker_name(args.delivery_test_id, args.publication_mode)
                   if args.delivery_test_id else delivery_marker_name(delivery_date, args.publication_mode))
    marker_already_exists = False
    recover_marker = False
    # Validation exercises the same downstream build route, but the workflow
    # independently disables every mail/receipt/intent/marker write step.
    if not args.correction and not args.validation_only:
        if not args.repository or not args.token:
            raise SystemExit("GITHUB_REPOSITORY and GITHUB_TOKEN are required for scheduled delivery checks")
        payload = fetch_artifacts(args.repository, args.token, marker_name, args.api_url)
        marker_already_exists = marker_exists(payload, marker_name)
        if not marker_already_exists and not args.delivery_test_id:
            legacy_name = delivery_marker_name(delivery_date)
            if marker_name != legacy_name:
                legacy = fetch_artifacts(args.repository, args.token, legacy_name, args.api_url)
                matches = [item for item in legacy.get("artifacts", []) if isinstance(item, dict)
                           and item.get("name") == legacy_name and item.get("expired") is not True]
                for item in matches:
                    if legacy_marker_mode(args.repository, args.token, args.api_url, item, delivery_date) == args.publication_mode:
                        marker_already_exists = True
                        break
        if not marker_already_exists:
            receipt_name = marker_name + "-smtp-accepted"
            receipts = fetch_artifacts(args.repository, args.token, receipt_name, args.api_url)
            if marker_exists(receipts, receipt_name):
                matches = [item for item in receipts["artifacts"]
                           if item.get("name") == receipt_name and item.get("expired") is not True]
                for item in matches:
                    verify_smtp_receipt(args.repository, args.token, args.api_url, item,
                                        delivery_date, args.publication_mode, args.delivery_test_id)
                marker_already_exists = True
                recover_marker = True
        if not marker_already_exists:
            intent_name = marker_name + "-send-intent"
            pending = fetch_artifacts(args.repository, args.token, intent_name, args.api_url)
            if marker_exists(pending, intent_name):
                raise RuntimeError("BLOCKED: prior SMTP send intent has no completion marker; delivery is uncertain, reconcile Gmail before explicit correction")
            if not args.delivery_test_id and args.publication_mode == "close_confirmed" and delivery_date == date(2026, 11, 2):
                # The completed one-off recovery used a different intent name.
                # Keep unknown old sends fail-closed even after retiring it.
                recovery_intent = "microcap-close-recovery-20261102-send-intent"
                pending = fetch_artifacts(args.repository, args.token, recovery_intent, args.api_url)
                if marker_exists(pending, recovery_intent):
                    raise RuntimeError("BLOCKED: recovery SMTP intent has no completion marker; reconcile Gmail before explicit correction")

    send = should_send(
        correction=args.correction,
        marker_already_exists=marker_already_exists,
    )
    write_outputs(
        {
            "should_send": str(send).lower(),
            "delivery_date": delivery_date.isoformat(),
            "marker_name": marker_name,
            "subject_prefix": ("发送验收测试" if args.delivery_test_id else
                               "验收不发送" if args.validation_only else ("纠正版" if args.correction else "")),
            "validation_only": str(args.validation_only).lower(),
            "delivery_test_id": args.delivery_test_id,
            "recover_marker": str(recover_marker).lower(),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
