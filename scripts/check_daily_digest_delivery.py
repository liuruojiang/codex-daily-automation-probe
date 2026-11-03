"""Serialize in the workflow; reconcile prior delivery before either scheduler sends.

A failed/cancelled send is deliberately not retried automatically. SMTP acceptance
can precede a runner failure, so absence of a receipt is not evidence of non-send.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

FAMILIES = {
    "etf": ("etf-allocation-digest.yml", "us-etf-allocation-digest", "美股 ETF 与资产配置日报 - "),
    "ai-hot": ("ai-hot-digest.yml", "ai-hot-digest", "AI HOT 日报 - "),
}
BJ = ZoneInfo("Asia/Shanghai")


class SafeArtifactRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and urllib.parse.urlsplit(req.full_url).netloc != urllib.parse.urlsplit(newurl).netloc:
            redirected.remove_header("Authorization")
        return redirected


class GitHub:
    def __init__(self, repository: str, token: str):
        self.base = f"https://api.github.com/repos/{repository}/actions"
        self.token = token

    def get(self, path: str, *, raw: bool = False):
        # Cached API redirects can contain expired signed storage URLs.
        if raw:
            path += ("&" if "?" in path else "?") + f"nonce={time.time_ns()}"
        req = urllib.request.Request(self.base + path, headers={
            "Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "daily-digest-delivery-gate",
        })
        opener = urllib.request.build_opener(SafeArtifactRedirect())
        with opener.open(req, timeout=60) as response:
            data = response.read(32 * 1024 * 1024 + 1)
        if len(data) > 32 * 1024 * 1024:
            raise RuntimeError("BLOCKED: GitHub evidence response exceeds size limit")
        return data if raw else json.loads(data)

    def pages(self, path: str, key: str):
        separator = "&" if "?" in path else "?"
        for page in range(1, 101):
            payload = self.get(f"{path}{separator}per_page=100&page={page}")
            rows = payload.get(key)
            if not isinstance(rows, list):
                raise RuntimeError(f"Invalid GitHub {key} response")
            yield from rows
            if len(rows) < 100:
                return
        raise RuntimeError("BLOCKED: delivery history pagination limit reached")


def report_day(metadata: dict, family: str) -> date:
    prefix = FAMILIES[family][2]
    subject = metadata.get("subject", "")
    if not isinstance(subject, str) or not subject.startswith(prefix):
        raise ValueError("Unexpected daily report subject")
    value = subject[len(prefix):]
    day = date.fromisoformat(value)
    if value != day.isoformat() or not isinstance(metadata.get("body"), str) or not metadata["body"].strip():
        raise ValueError("Invalid daily report date/body")
    if metadata.get("attachment") is not None:
        raise ValueError("Daily digest must not have an attachment")
    return day


def read_artifact(api: GitHub, artifact: dict) -> dict:
    raw = api.get(f"/artifacts/{int(artifact['id'])}/zip", raw=True)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        result = {}
        for name in ("metadata.json", "delivery_receipt.json", "send_intent.json"):
            matches = [entry for entry in archive.infolist()
                       if entry.filename in (name, "artifacts/" + name)]
            if len(matches) > 1 or any(entry.file_size > 16 * 1024 * 1024 for entry in matches):
                raise RuntimeError("BLOCKED: ambiguous or oversized delivery evidence")
            if matches:
                data = archive.read(matches[0])
                result[name] = (data, json.loads(data))
        return result


def accepted_receipt(values: dict, run: dict) -> bool:
    if "metadata.json" not in values or "delivery_receipt.json" not in values:
        return False
    raw, meta = values["metadata.json"]
    _, receipt = values["delivery_receipt.json"]
    try:
        stamp = datetime.fromisoformat(receipt["accepted_at"])
        started = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
        updated = datetime.fromisoformat(run["updated_at"].replace("Z", "+00:00"))
        return (receipt.get("schema_version") == 1 and receipt.get("status") == "smtp_accepted"
                and stamp.tzinfo is not None and started <= stamp <= updated
                and re.fullmatch(r"<[^<>\s]+@[^<>\s]+>", receipt.get("message_id", "")) is not None
                and receipt.get("metadata_sha256") == hashlib.sha256(raw).hexdigest()
                and receipt.get("subject_sha256") == hashlib.sha256(meta["subject"].encode()).hexdigest()
                and receipt.get("body_sha256") == hashlib.sha256(meta["body"].encode()).hexdigest())
    except (KeyError, ValueError, TypeError):
        return False


def verified_intent_day(values: dict, run: dict, family: str) -> date:
    if "metadata.json" not in values or "send_intent.json" not in values:
        raise RuntimeError("BLOCKED: send intent lacks metadata or intent document")
    raw, metadata = values["metadata.json"]
    _, intent = values["send_intent.json"]
    day = report_day(metadata, family)
    if (intent.get("schema_version") != 1 or intent.get("status") != "send_intent"
            or intent.get("family") != family or intent.get("report_date") != day.isoformat()
            or str(intent.get("run_id")) != str(run["id"])
            or str(intent.get("run_attempt")) != str(run.get("run_attempt", 1))
            or intent.get("head_sha") != run["head_sha"]
            or intent.get("workflow") != FAMILIES[family][0]
            or intent.get("metadata_sha256") != hashlib.sha256(raw).hexdigest()):
        raise RuntimeError("BLOCKED: send intent identity/hash mismatch")
    return day


def reconcile(api: GitHub, repository: str, family: str, metadata: dict) -> dict:
    workflow, artifact_name, _ = FAMILIES[family]
    day = report_day(metadata, family)
    since = (day - timedelta(days=1)).isoformat()
    runs = api.pages(f"/workflows/{workflow}/runs?branch=main&created={urllib.parse.quote('>=' + since)}", "workflow_runs")
    uncertain = []
    confirmed = []
    for run in runs:
        if (run.get("path") != f".github/workflows/{workflow}" or run.get("head_branch") != "main"
                or run.get("event") not in ("schedule", "workflow_dispatch")
                or run.get("repository", {}).get("full_name") != repository
                or run.get("head_repository", {}).get("full_name") != repository
                or not re.fullmatch(r"[0-9a-f]{40}", run.get("head_sha", ""))):
            raise RuntimeError("BLOCKED: unexpected production run identity")
        jobs = list(api.pages(f"/runs/{run['id']}/jobs?filter=all", "jobs"))
        sends = [step for job in jobs if job.get("name") == "send"
                 for step in job.get("steps", []) if step.get("name") == "Send Gmail"
                 and step.get("started_at") and step.get("conclusion") != "skipped"]
        if not sends:
            continue
        # GitHub run artifacts can outlive their attempt while jobs?filter=all
        # mixes attempts. Do not pair an earlier successful send with a newer
        # body's evidence or exclude a newer uncertain send using an older body.
        if run.get("run_attempt", 1) != 1:
            raise RuntimeError(f"BLOCKED: delivery_unknown for rerun {run['id']}; mixed-attempt send evidence requires reconciliation")
        intent_name = f"{family}-send-intent-{run['id']}-{run.get('run_attempt', 1)}"
        artifacts = [item for item in api.pages(f"/runs/{run['id']}/artifacts", "artifacts")
                     if item.get("name") in (artifact_name, intent_name) and item.get("expired") is not True]
        matches = []
        canonical_days = []
        intent_days = []
        missing_metadata = False
        for artifact in artifacts:
            identity = artifact.get("workflow_run", {})
            if (identity.get("id") != run["id"] or identity.get("head_sha") != run["head_sha"]
                    or identity.get("head_branch") != "main"):
                raise RuntimeError("BLOCKED: report artifact/run identity mismatch")
            values = read_artifact(api, artifact)
            if artifact.get("name") == intent_name:
                intent_days.append(verified_intent_day(values, run, family))
            elif "metadata.json" in values:
                _, prior = values["metadata.json"]
                prior_day = report_day(prior, family)
                canonical_days.append(prior_day)
                if prior_day == day:
                    matches.append(values)
            else:
                missing_metadata = True
        evidence_days = set(canonical_days + intent_days)
        if len(evidence_days) > 1:
            raise RuntimeError(f"BLOCKED: delivery_unknown for run {run['id']}; conflicting report dates")
        if not matches:
            # A bound intent can exclude an older report even when the final
            # report upload failed. It cannot establish SMTP acceptance.
            if not evidence_days or day in evidence_days or missing_metadata:
                uncertain.append(run["id"])
            continue
        if any(step.get("conclusion") == "success" for step in sends) or any(accepted_receipt(v, run) for v in matches):
            confirmed.append(run["id"])
        else:
            uncertain.append(run["id"])
    if confirmed:
        return {"should_send": "false", "delivery_state": "sent_confirmed", "prior_run_id": str(confirmed[0])}
    if uncertain:
        raise RuntimeError(f"BLOCKED: delivery_unknown for prior runs {uncertain}; reconcile delivery, do not resend")
    return {"should_send": "true", "delivery_state": "not_sent", "prior_run_id": ""}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--family", choices=FAMILIES, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--validation-only", action="store_true")
    args = parser.parse_args()
    raw = args.metadata.read_bytes()
    metadata = json.loads(raw)
    day = report_day(metadata, args.family)
    if args.validation_only:
        values = {"should_send": "false", "delivery_state": "validation_only", "prior_run_id": ""}
    else:
        repository = os.environ["GITHUB_REPOSITORY"]
        if repository != "liuruojiang/codex-daily-automation-probe" or os.environ.get("GITHUB_REF") != "refs/heads/main":
            raise RuntimeError("BLOCKED: production send requires the trusted repository main branch")
        values = reconcile(GitHub(repository, os.environ["GITHUB_TOKEN"]), repository, args.family, metadata)
    output = os.environ.get("GITHUB_OUTPUT")
    rendered = "".join(f"{key}={value}\n" for key, value in values.items())
    if output:
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(rendered)
    print(rendered, end="")
    if values["should_send"] == "true":
        intent = {"schema_version": 1, "status": "send_intent", "family": args.family,
                  "report_date": day.isoformat(), "run_id": os.environ["GITHUB_RUN_ID"],
                  "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"], "head_sha": os.environ["GITHUB_SHA"],
                  "workflow": FAMILIES[args.family][0], "metadata_sha256": hashlib.sha256(raw).hexdigest(),
                  "created_at": datetime.now(timezone.utc).isoformat()}
        (args.metadata.parent / "send_intent.json").write_text(json.dumps(intent, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
