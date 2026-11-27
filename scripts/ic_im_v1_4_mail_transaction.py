"""Bind IC/IM normal mail and failure alerts to independent delivery transactions."""
from __future__ import annotations

import hashlib
import io
import json
import re
import urllib.request
import zipfile
from datetime import date, datetime
from pathlib import Path

from check_ic_im_v1_4_delivery import (
    delivery_date,
    expected_identity_for_day,
    marker_prefix,
    now_utc,
)


SCHEMA = "icim_v1_4_mail_transaction_v1"
WORKFLOW = ".github/workflows/ic-im-v1-4-daily-digest.yml"
RECEIPT_SUFFIX = "-smtp-accepted"
INTENT_SUFFIX = "-send-intent"
MAX_RECEIPT_BYTES = 4 * 1024 * 1024


def alert_marker(day: date, mode: str) -> str:
    return marker_prefix(day, mode).replace("-digest-delivered-", "-failure-alert-delivered-").removesuffix("-")


def _marker(day: date, mode: str, kind: str, digest: str | None) -> str:
    if kind == "normal" and isinstance(digest, str) and re.fullmatch("[a-f0-9]{64}", digest):
        return marker_prefix(day, mode) + digest[:12]
    if kind == "failure" and digest is None:
        return alert_marker(day, mode)
    raise ValueError("Invalid IC/IM mail kind or signal digest")


def build_transaction(result, metadata: bytes, day: date, mode: str, kind: str) -> dict:
    build, revision = expected_identity_for_day(day)
    if (result.get("market_date") != day.isoformat() or result.get("publication_mode") != mode
            or result.get("strategy_revision") != "r1" or result.get("build") != build
            or result.get("delivery_revision") != revision):
        raise ValueError("IC/IM mail result differs from the selected delivery date, mode or revision")
    digest = None
    if kind == "normal":
        from prepare_ic_im_v1_4_marker import marker_name

        marker_name(result)
        digest = result["digest"]
    elif result.get("status") != "failed":
        raise ValueError("IC/IM failure alert requires a failed result")
    return {
        "schema": SCHEMA,
        "market_date": day.isoformat(),
        "publication_mode": mode,
        "kind": kind,
        "digest": digest,
        "marker_name": _marker(day, mode, kind, digest),
        "metadata_sha256": hashlib.sha256(metadata).hexdigest(),
    }


def validate_transaction(transaction: dict, metadata: bytes, day: date, mode: str, kind: str) -> str:
    marker = _marker(day, mode, kind, transaction.get("digest"))
    if transaction != {
        "schema": SCHEMA,
        "market_date": day.isoformat(),
        "publication_mode": mode,
        "kind": kind,
        "digest": transaction.get("digest"),
        "marker_name": marker,
        "metadata_sha256": hashlib.sha256(metadata).hexdigest(),
    }:
        raise ValueError("IC/IM mail transaction metadata, due date or marker differs")
    return marker


def check_transaction(artifacts, day: date, mode: str, kind: str, verify_receipt):
    """Return status and a recovered marker, after checking all day-level evidence."""
    base = marker_prefix(day, mode) if kind == "normal" else alert_marker(day, mode)
    target = re.escape(base) + ("[a-f0-9]{12}" if kind == "normal" else "")
    candidates: dict[str, list[dict]] = {}
    for item in artifacts["artifacts"]:
        if not isinstance(item, dict) or item.get("expired") is True:
            continue
        name = item.get("name")
        if isinstance(name, str) and re.fullmatch(target + "(?:-smtp-accepted|-send-intent)?", name):
            candidates.setdefault(name, []).append(item)
    if any(re.fullmatch(target, name) for name in candidates):
        return "delivered", None
    receipts = [item for name, items in candidates.items() if name.endswith(RECEIPT_SUFFIX) for item in items]
    if len(receipts) > 1:
        raise RuntimeError("BLOCKED: competing IC/IM SMTP receipts require reconciliation")
    recovered = verify_receipt(receipts[0], day, mode, kind) if receipts else None
    resolved = recovered["marker_name"] if recovered else None
    if any(name.endswith(INTENT_SUFFIX) and name.removesuffix(INTENT_SUFFIX) != resolved
           for name in candidates):
        raise RuntimeError("BLOCKED: prior IC/IM SMTP intent has an uncertain outcome; reconcile the original message")
    if recovered:
        return "recover_marker", recovered
    return "due", None


def _timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("SMTP transaction timestamp lacks a timezone")
    return result


def _read_github(url, token):
    from restore_ic_im_v1_3_ledger import api_request

    with urllib.request.urlopen(api_request(url, token), timeout=30) as response:
        return json.load(response)


def verify_smtp_receipt(repository, token, api_url, artifact, day, mode, kind) -> dict:
    from restore_ic_im_v1_3_ledger import download

    base = f"{api_url.rstrip('/')}/repos/{repository}/actions"
    artifact_id = artifact.get("id")
    artifact_run = artifact.get("workflow_run") or {}
    run_id = artifact_run.get("id")
    size = artifact.get("size_in_bytes")
    digest = artifact.get("digest", "")
    if (type(artifact_id) is not int or artifact_id <= 0 or type(run_id) is not int or run_id <= 0
            or artifact.get("archive_download_url") != f"{base}/artifacts/{artifact_id}/zip"
            or type(size) is not int or not 0 < size <= MAX_RECEIPT_BYTES
            or not isinstance(digest, str) or re.fullmatch("sha256:[a-f0-9]{64}", digest) is None):
        raise RuntimeError("BLOCKED: IC/IM SMTP receipt artifact identity differs")
    run = _read_github(f"{base}/runs/{run_id}", token)
    head = run.get("head_sha", "")
    if (run.get("id") != run_id or run.get("status") != "completed" or run.get("head_branch") != "main"
            or re.fullmatch("[a-f0-9]{40}", head) is None or artifact_run.get("head_sha") != head
            or str(run.get("path", "")).split("@", 1)[0] != WORKFLOW
            or run.get("event") not in {"schedule", "workflow_dispatch"}
            or (run.get("repository") or {}).get("full_name") != repository):
        raise RuntimeError("BLOCKED: IC/IM receipt has no trusted digest run")
    jobs = _read_github(f"{base}/runs/{run_id}/jobs?filter=all&per_page=100", token)
    if jobs.get("total_count", 100) > 100:
        raise RuntimeError("BLOCKED: ambiguous IC/IM send attempts for SMTP receipt")
    # A rerun can follow a receipt upload. Select the send job whose verified
    # interval contains the acceptance time, rather than borrowing a newer job.
    send_jobs = [job for job in jobs.get("jobs", []) if job.get("name") == "send"]
    raw = download(artifact, token)
    if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest[7:]:
        raise RuntimeError("BLOCKED: IC/IM SMTP receipt size or hash differs")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = {}
        for name, limit in (("metadata.json", MAX_RECEIPT_BYTES),
                            ("delivery-transaction.json", 65536), ("smtp-accepted.json", 65536)):
            files = [item for item in archive.infolist() if item.filename == name]
            if len(files) != 1 or files[0].file_size > limit:
                raise RuntimeError("BLOCKED: IC/IM SMTP receipt archive has missing or oversized evidence")
            entries[name] = archive.read(files[0])
    metadata_raw = entries["metadata.json"]
    metadata = json.loads(metadata_raw)
    transaction = json.loads(entries["delivery-transaction.json"])
    receipt = json.loads(entries["smtp-accepted.json"])
    marker = validate_transaction(transaction, metadata_raw, day, mode, kind)
    accepted = _timestamp(receipt["accepted_at"])
    acceptance_second = accepted.replace(microsecond=0)
    intent_name = ("Persist normal-send intent before SMTP" if kind == "normal"
                   else "Persist failure-alert intent before SMTP")
    valid_jobs = 0
    for job in send_jobs:
        steps = {step.get("name"): step for step in job.get("steps", [])}
        send = steps.get("Send Gmail", {})
        preserved = steps.get("Preserve accepted SMTP receipt", {})
        if (send.get("conclusion") == "success" and preserved.get("conclusion") == "success"
                and steps.get(intent_name, {}).get("conclusion") == "success"):
            # Actions exposes step bounds at second precision. A receipt may
            # include subsecond precision within the same terminal second.
            start = _timestamp(send["started_at"]).replace(microsecond=0)
            end = _timestamp(send["completed_at"]).replace(microsecond=0)
            if start <= acceptance_second <= end:
                valid_jobs += 1
    build, revision = expected_identity_for_day(day)
    if (valid_jobs != 1 or artifact.get("name") != marker + RECEIPT_SUFFIX
            or accepted > now_utc() or delivery_date(accepted) < day
            or (delivery_date(accepted) > day and mode != "close_confirmed")
            or type(receipt.get("schema_version")) is not int or receipt["schema_version"] != 1
            or receipt.get("status") != "smtp_accepted"
            or not isinstance(receipt.get("message_id"), str)
            or re.fullmatch(r"<[^<>\s]+@[^<>\s]+>", receipt["message_id"]) is None
            or receipt.get("icim_delivery_marker") != marker
            or receipt.get("metadata_sha256") != transaction["metadata_sha256"]
            or receipt.get("subject_sha256") != hashlib.sha256(metadata["subject"].encode()).hexdigest()
            or receipt.get("body_sha256") != hashlib.sha256(metadata["body"].encode()).hexdigest()):
        raise RuntimeError("BLOCKED: IC/IM SMTP receipt does not prove acceptance for this delivery")
    return {
        "marker_name": marker,
        "strategy_revision": "r1",
        "build": build,
        "delivery_revision": revision,
        "market_date": day.isoformat(),
        "publication_mode": mode,
        "kind": kind,
        "digest": transaction["digest"],
        "message_id": receipt["message_id"],
        "accepted_at": receipt["accepted_at"],
        "receipt_artifact_id": artifact_id,
        "receipt_run_id": run_id,
    }


def write_recovered_marker(directory: Path | None, recovered: dict | None) -> None:
    if recovered is None or directory is None:
        return
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "delivery.json").write_text(json.dumps(recovered, indent=2) + "\n", encoding="utf-8")
