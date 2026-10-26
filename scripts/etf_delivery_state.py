"""Separate SMTP acceptance, post-send failures and uncertain delivery.

The GitHub Contents ledger is created before SMTP, not after it. An existing
logical-day claim blocks every automatic re-send, including another runner/run.
This is fail-closed at-most-once dispatch, not guaranteed exactly-once delivery.
"""
from __future__ import annotations

import argparse
import base64
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse

from github_metadata import github_request

SCHEMA = "etf-durable-delivery-v1"
BASELINE_SCHEMA = "etf-delivery-activation-v1"
WORKFLOW = ".github/workflows/etf-allocation-digest.yml"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DeliveryBlocked(RuntimeError):
    pass


def make_intent(metadata_bytes, metadata, sender, recipients, message_id, environ=None):
    env = os.environ if environ is None else environ
    repo, branch = env.get("GITHUB_REPOSITORY", ""), env.get("DEFAULT_BRANCH", "")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo) or not re.fullmatch(r"[\w./-]+", branch):
        raise DeliveryBlocked("Invalid durable delivery repository/branch")
    if env.get("GITHUB_REF_NAME") != branch or env.get("GITHUB_EVENT_NAME") not in ("schedule", "workflow_dispatch"):
        raise DeliveryBlocked("Durable ETF sending requires a trusted default-branch run")
    if env.get("GITHUB_WORKFLOW_REF") != repo + "/" + WORKFLOW + "@refs/heads/" + branch:
        raise DeliveryBlocked("Unexpected durable delivery workflow")
    run_id, attempt = env.get("GITHUB_RUN_ID", ""), env.get("GITHUB_RUN_ATTEMPT", "")
    sha = env.get("GITHUB_SHA", "")
    if not run_id.isdigit() or int(run_id) <= 0 or not attempt.isdigit() or int(attempt) <= 0 or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise DeliveryBlocked("Invalid durable delivery producer identity")
    match = re.fullmatch(r"美股 ETF 与资产配置日报 - (\d{4}-\d{2}-\d{2})", metadata["subject"])
    if not match:
        raise DeliveryBlocked("Durable ETF sender requires the exact dated production subject")
    report_date = date.fromisoformat(match[1]).isoformat()
    destination_hash = digest({"sender": sender.lower(), "recipients": sorted(set(x.lower() for x in recipients))})
    # Deliberately exclude body hash and run ID: a rebuild of the same report day
    # must not evade a previous uncertain/accepted send merely by changing content.
    key = digest({"schema": SCHEMA, "repository": repo, "report_date": report_date, "destination_sha256": destination_hash})
    return {"schema_version": SCHEMA, "logical_key": key, "state": "in_flight", "created_at": now(),
            "repository": repo, "branch": branch, "workflow_path": WORKFLOW, "report_date": report_date,
            "source_run_id": run_id, "source_run_attempt": int(attempt), "source_head_sha": sha,
            "message_id": message_id, "destination_sha256": destination_hash,
            "metadata_sha256": hashlib.sha256(metadata_bytes).hexdigest(),
            "subject_sha256": hashlib.sha256(metadata["subject"].encode()).hexdigest(),
            "body_sha256": hashlib.sha256(metadata["body"].encode()).hexdigest()}


class GitHubLedger:
    """One committed file per logical send; optimistic CAS guards races."""
    def __init__(self, intent, token, transport=github_request):
        if not token:
            raise DeliveryBlocked("Durable delivery requires the workflow token")
        self.intent, self.token, self.transport = intent, token, transport
        self.url = "https://api.github.com/repos/" + intent["repository"] + "/contents/digest_history/etf_delivery/" + intent["logical_key"] + ".json"
        self.sha = None

    def verify_activation(self):
        """No implicit migration: old unguarded mail requires a reviewed cutover."""
        url = "https://api.github.com/repos/" + self.intent["repository"] + "/contents/digest_history/etf_delivery/activation.json"
        try:
            status, result = self.transport(url + "?ref=" + urllib.parse.quote(self.intent["branch"], safe=""), self.token)
            if status != 200 or result.get("encoding") != "base64":
                raise ValueError("Activation is not a repository JSON file")
            activation = json.loads(base64.b64decode(result["content"], validate=False))
            first = date.fromisoformat(activation["first_report_date"])
            through = date.fromisoformat(activation["legacy_coverage_through"])
            report = date.fromisoformat(self.intent["report_date"])
            reviewed = datetime.fromisoformat(activation["reviewed_at"].replace("Z", "+00:00"))
            if (activation.get("schema_version") != BASELINE_SCHEMA or activation.get("state") != "approved"
                    or activation.get("repository") != self.intent["repository"]
                    or activation.get("branch") != self.intent["branch"]
                    or activation.get("workflow_path") != WORKFLOW
                    or activation.get("legacy_sender_stopped") is not True
                    or not isinstance(activation.get("legacy_evidence"), str) or not activation["legacy_evidence"].strip()
                    or reviewed.tzinfo is None or reviewed > datetime.now(timezone.utc) or not reviewed.date() < first
                    or (first - through).days != 1 or report < first):
                raise ValueError("Unapproved, mismatched or historical activation")
        except Exception:
            raise DeliveryBlocked("Verified ETF activation missing/invalid or report predates cutover; SMTP was not attempted") from None

    def claim(self):
        self.verify_activation()
        try:
            self.transport(self.url + "?ref=" + urllib.parse.quote(self.intent["branch"], safe=""), self.token)
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise DeliveryBlocked("Cannot verify prior durable delivery state") from None
        except Exception:
            raise DeliveryBlocked("Cannot verify prior durable delivery state") from None
        else:
            raise DeliveryBlocked("A durable intent already exists for this report day; reconcile it before any resend")
        try:
            status, result = self.transport(self.url, self.token, method="PUT", data=self._payload(self.intent))
            if status != 201 or not isinstance(result.get("content", {}).get("sha"), str):
                raise ValueError("Claim was not confirmed")
            self.sha = result["content"]["sha"]
        except Exception:
            # A timed-out PUT might have committed. Never send on an ambiguous claim.
            raise DeliveryBlocked("Durable delivery claim not confirmed; SMTP was not attempted") from None

    def _payload(self, value):
        result = {"branch": self.intent["branch"], "message": "Record ETF delivery state [skip ci]",
                  "content": base64.b64encode((json.dumps(value, sort_keys=True, indent=2) + "\n").encode()).decode()}
        if self.sha is not None:result["sha"] = self.sha
        return result

    def finish(self, state):
        if self.sha is None or state not in ("smtp_accepted", "delivery_unknown"):
            raise DeliveryBlocked("No verified claim for delivery completion")
        value = {**self.intent, "state": state, "updated_at": now()}
        status, result = self.transport(self.url, self.token, method="PUT", data=self._payload(value))
        if status != 200 or not isinstance(result.get("content", {}).get("sha"), str):
            raise DeliveryBlocked("Durable delivery completion not confirmed; retain the original claim")
        self.sha = result["content"]["sha"]


def receipt_matches(run_id, head, receipt, *, body_sha256=None, subject_sha256=None):
    return (isinstance(receipt, dict) and receipt.get("schema_version") == 1 and receipt.get("status") == "smtp_accepted"
        and str(receipt.get("source_run_id")) == str(run_id) and receipt.get("source_head_sha") == head
        and receipt.get("workflow_path") == WORKFLOW and isinstance(receipt.get("message_id"), str)
        and re.fullmatch(r"<[^<>\s]+@[^<>\s]+>", receipt["message_id"]) is not None
        and (body_sha256 is None or receipt.get("body_sha256") == body_sha256)
        and (subject_sha256 is None or receipt.get("subject_sha256") == subject_sha256))


def classify_delivery(run, jobs, receipt=None, intent=None):
    """Requires independently fetched run/jobs and source-bound receipt inputs.

    Classifying delivery does not validate a manifest, report date or source
    producer. Those integrity gates remain mandatory and separate.
    """
    rows = jobs.get("jobs", []) if isinstance(jobs, dict) else jobs
    run_id = str(run.get("id", run.get("run_id", "")))
    head = run.get("head_sha")
    if not run_id.isdigit() or int(run_id) <= 0 or not isinstance(rows, list):
        raise ValueError("Delivery classification requires a positive run and API jobs")
    if any(str(job.get("run_id", "")) != run_id for job in rows):
        raise ValueError("Delivery jobs do not belong to this run")
    steps = [step for job in rows for step in job.get("steps", []) if step.get("name") == "Send Gmail"]
    if len(steps) != 1:
        return {"status": "unverified", "reason": "send_step_missing_or_ambiguous", "post_send_failure": False}
    conclusion = steps[0].get("conclusion")
    bound_receipt = receipt_matches(run_id, head, receipt)
    bound_intent = (isinstance(intent, dict) and intent.get("schema_version") == SCHEMA
        and str(intent.get("source_run_id")) == run_id and intent.get("source_head_sha") == head
        and intent.get("workflow_path") == WORKFLOW)
    if conclusion == "success" or bound_receipt:
        failures = ("failure", "cancelled", "timed_out", "action_required", "startup_failure", "stale")
        return {"status": "sent_confirmed", "send_gmail": conclusion, "workflow_conclusion": run.get("conclusion"),
                "evidence": "bound_smtp_receipt" if bound_receipt else "github_send_step_success",
                "post_send_failure": run.get("conclusion") in failures or conclusion in failures}
    if bound_intent or conclusion in ("failure", "cancelled", "timed_out") or steps[0].get("status") == "in_progress":
        return {"status": "delivery_unknown", "reason": "send_not_confirmed_do_not_retry", "post_send_failure": False}
    if conclusion == "skipped":
        return {"status": "not_sent", "reason": "send_step_skipped", "post_send_failure": False}
    return {"status": "unverified", "reason": "insufficient_send_evidence", "post_send_failure": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True);parser.add_argument("--jobs", type=Path, required=True)
    parser.add_argument("--receipt", type=Path);parser.add_argument("--intent", type=Path)
    args = parser.parse_args()
    read = lambda path: json.loads(path.read_text(encoding="utf-8")) if path else None
    print(json.dumps(classify_delivery(read(args.run), read(args.jobs), read(args.receipt), read(args.intent))))


if __name__ == "__main__":main()
