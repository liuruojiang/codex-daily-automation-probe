"""Weekly-only at-most-once delivery, with persistent claims and uncertain-send blocking."""

import argparse
import base64
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid
import zipfile
import zlib

from delivery_contract import verify_report_binding

REPOSITORY = "liuruojiang/codex-daily-automation-probe"
WORKFLOW = "equity-momentum-boundary-weekly.yml"
REF_PREFIX = "tags/equity-boundary-delivery-v1/"
REPLAY_PATH = "monitoring/equity_momentum_boundary/replay_request.json"
BJ = timezone(timedelta(hours=8))


class DeliveryUnknown(RuntimeError):
    """The same delivery identity must not be sent again automatically."""


class ApiFailure(RuntimeError):
    def __init__(self, status=None):
        self.status = status
        super().__init__(f"GitHub request failed (status={status or 'unknown'})")


def api(repository, path, method="GET", payload=None, raw=False):
    command = ["gh", "api", f"repos/{repository}/{path}", "--method", method]
    if payload is not None:
        command += ["--input", "-"]
    try:
        result = subprocess.run(command, input=json.dumps(payload).encode() if payload is not None else None,
                                capture_output=True, check=True, timeout=60)
    except subprocess.CalledProcessError as error:
        match = re.search(rb"HTTP (\d{3})", error.stderr or b"")
        raise ApiFailure(int(match[1]) if match else None) from None
    except (OSError, subprocess.TimeoutExpired):
        raise ApiFailure() from None
    try:
        return result.stdout if raw else json.loads(result.stdout)
    except ValueError:
        raise ApiFailure() from None


def stamp(value):
    if not isinstance(value, str):
        raise ValueError("Invalid timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timezone required")
    return result


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def envelope(environment):
    def addresses(value):
        if not isinstance(value, str) or any(x in value for x in "\r\n"):
            raise ValueError("Invalid mail address configuration")
        result = []
        for _, address in getaddresses([value]):
            if address.count("@") != 1 or any(x.isspace() for x in address):
                raise ValueError("Invalid mail address configuration")
            local, domain = address.rsplit("@", 1)
            if not local or not domain:
                raise ValueError("Invalid mail address configuration")
            result.append(local + "@" + domain.lower())
        return sorted(set(result))
    sender, recipients = addresses(environment.get("MAIL_FROM")), addresses(environment.get("MAIL_TO"))
    if len(sender) != 1 or not recipients:
        raise ValueError("Invalid sender or recipients")
    return {"sender": sender[0], "recipients": recipients}


class GitJournal:
    """Exact-ref creation is atomic; completion is a fast-forward child of that claim."""
    def __init__(self, repository=REPOSITORY):
        if repository != REPOSITORY:
            raise ValueError("Unexpected delivery repository")
        self.repository = repository

    def request(self, path, method="GET", payload=None):
        return api(self.repository, path, method, payload)

    def read(self, key):
        if re.fullmatch(r"[0-9a-f]{64}", key) is None:
            raise ValueError("Invalid delivery key")
        try:
            ref = self.request("git/ref/" + REF_PREFIX + key)
        except ApiFailure as error:
            if error.status == 404:
                return None
            raise DeliveryUnknown("Cannot read persistent delivery state") from None
        try:
            sha = ref["object"]["sha"]
            commit = self.request("git/commits/" + sha)
            tree = self.request("git/trees/" + commit["tree"]["sha"])["tree"]
            if len(tree) != 1 or tree[0]["path"] != "transaction.json" or tree[0]["type"] != "blob":
                raise ValueError("Invalid transaction tree")
            blob = self.request("git/blobs/" + tree[0]["sha"])
            state = json.loads(base64.b64decode(blob["content"], validate=False))
            if not isinstance(state, dict) or state.get("schema_version") != 1 or state.get("key") != key:
                raise ValueError("Invalid transaction identity")
            return sha, state
        except (ApiFailure, KeyError, ValueError, TypeError):
            raise DeliveryUnknown("Persistent delivery record is incomplete or unreadable") from None

    def commit(self, state, parent=None):
        blob = self.request("git/blobs", "POST", {"content": json.dumps(state, ensure_ascii=False), "encoding": "utf-8"})
        tree = self.request("git/trees", "POST", {"tree": [{"path": "transaction.json", "mode": "100644", "type": "blob", "sha": blob["sha"]}]})
        return self.request("git/commits", "POST", {"message": "Weekly boundary delivery transaction", "tree": tree["sha"], "parents": [parent] if parent else []})["sha"]

    def claim(self, key, state):
        try:
            sha = self.commit(state)
            self.request("git/refs", "POST", {"ref": "refs/" + REF_PREFIX + key, "sha": sha})
        except (ApiFailure, KeyError, ValueError, TypeError):
            # A lost POST response is not proof of failure. Only this random owner may continue.
            current = self.read(key)
            if current and current[1].get("owner") == state["owner"] and current[1] == state:
                return current[0]
            raise DeliveryUnknown("Delivery claim already exists or its creation is uncertain") from None
        current = self.read(key)
        if current is None or current[0] != sha or current[1] != state:
            raise DeliveryUnknown("Cannot confirm ownership of delivery claim")
        return sha

    def finish(self, key, claim_sha, state):
        try:
            sha = self.commit(state, claim_sha)
            self.request("git/refs/" + REF_PREFIX + key, "PATCH", {"sha": sha, "force": False})
        except (ApiFailure, KeyError, ValueError, TypeError):
            current = self.read(key)
            if current and current[1] == state:
                return
            raise DeliveryUnknown("SMTP result may be accepted; persistent completion is uncertain, do not resend") from None
        current = self.read(key)
        if not current or current[0] != sha or current[1] != state:
            raise DeliveryUnknown("SMTP result may be accepted; cannot confirm persistent completion")


def pages(repository, path, field):
    for page in range(1, 6):
        payload = api(repository, path + ("&" if "?" in path else "?") + f"per_page=100&page={page}")
        rows = payload.get(field)
        if not isinstance(rows, list):
            raise DeliveryUnknown("Malformed delivery history")
        yield from rows
        if len(rows) < 100:
            return
    raise DeliveryUnknown("Delivery history pagination incomplete; refusing to assume no prior send")


def accepted_archive(raw, identity, now):
    """Legacy receipts may prevent duplicate mail, but never authenticate legacy report numbers."""
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = ("metadata.json", "delivery-receipt.json")
        if any(archive.namelist().count(name) != 1 for name in names):
            return False
        if any(archive.getinfo(name).file_size > 10_000_000 for name in names):
            raise DeliveryUnknown("Oversized delivery evidence")
        meta_raw, receipt_raw = (archive.read(name) for name in names)
    meta, receipt = json.loads(meta_raw), json.loads(receipt_raw)
    if (receipt.get("status") != "smtp_accepted"
            or not re.fullmatch(r"<[^<>\s]+@[^<>\s]+>", receipt.get("message_id", ""))
            or receipt.get("metadata_sha256") != hashlib.sha256(meta_raw).hexdigest()
            or receipt.get("body_sha256") != hashlib.sha256(meta["body"].encode()).hexdigest()
            or receipt.get("subject_sha256") != hashlib.sha256(meta["subject"].encode()).hexdigest()):
        raise DeliveryUnknown("Invalid historical SMTP evidence")
    accepted = stamp(receipt["accepted_at"])
    if accepted > now:
        raise DeliveryUnknown("Future SMTP receipt")
    # Use the report date in the actual delivered subject, not unbound report.json.
    match = re.fullmatch(r"每周股票动量边界监控 \| [^|]+ \| (\d{4}-\d{2}-\d{2})(?: \| .+)?", meta["subject"])
    if not match:
        raise DeliveryUnknown("Unexpected weekly delivery subject")
    generated_match = re.search(r"^生成时间（UTC）：([^\r\n]+)", meta["body"], flags=re.M)
    if not generated_match:
        raise DeliveryUnknown("Historical delivery has no bound generation timestamp")
    generated = stamp(generated_match[1])
    if generated > accepted or generated.astimezone(timezone.utc).date().isoformat() != match[1]:
        raise DeliveryUnknown("Historical delivery date does not match its bound body")
    week = generated.astimezone(BJ).isocalendar()[:2]
    prior_envelope = envelope({"MAIL_FROM": receipt["sender"], "MAIL_TO": ",".join(receipt["recipients"])})
    return list(week) == identity["iso_week"] and prior_envelope == identity["envelope"]


def historical_delivery(identity, now, environment):
    since = (now.astimezone(BJ).date() - timedelta(days=37)).isoformat()
    uncertain, confirmed = [], []
    runs = pages(REPOSITORY, f"actions/workflows/{WORKFLOW}/runs?branch=main&created=%3E%3D{since}", "workflow_runs")
    for run in runs:
        if run.get("head_branch") != "main" or run.get("path") != ".github/workflows/" + WORKFLOW:
            raise DeliveryUnknown("Unexpected weekly run identity")
        jobs = list(pages(REPOSITORY, f"actions/runs/{run['id']}/jobs?filter=all", "jobs"))
        sends = []
        for job in jobs:
            for step in job.get("steps", []):
                if step.get("name") != "Send through the existing daily-report mailer" or not step.get("started_at") or step.get("conclusion") == "skipped":
                    continue
                # The current attempt's send step is running this check; earlier attempts remain evidence.
                if str(run["id"]) == environment["GITHUB_RUN_ID"] and str(job.get("run_attempt")) == environment["GITHUB_RUN_ATTEMPT"]:
                    continue
                step_week = stamp(step["started_at"]).astimezone(BJ).isocalendar()[:2]
                if list(step_week) == identity["iso_week"]:
                    sends.append(step)
        matched = False
        artifacts = pages(REPOSITORY, f"actions/runs/{run['id']}/artifacts", "artifacts")
        for artifact in artifacts:
            if artifact.get("expired") or not re.fullmatch(rf"equity-momentum-boundary-{run['id']}(?:-\d+)?", artifact.get("name", "")):
                continue
            raw = api(REPOSITORY, f"actions/artifacts/{artifact['id']}/zip", raw=True)
            if accepted_archive(raw, identity, now):
                matched = True
                confirmed.append(run["id"])
        if sends and not matched:
            uncertain.append(run["id"])
    if confirmed:
        return {"status": "sent", "prior_run_id": confirmed[0]}
    if uncertain:
        return {"status": "unknown", "prior_runs": uncertain}
    return {"status": "not_sent"}


def replay_namespace(environment, report_time, now):
    if environment["GITHUB_EVENT_NAME"] != "push":
        return "normal"
    event = json.loads(Path(environment["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    request = json.loads((Path(__file__).resolve().parents[2] / REPLAY_PATH).read_text(encoding="utf-8"))
    before, after = event.get("before"), event.get("after")
    if (not isinstance(before, str) or re.fullmatch(r"[0-9a-f]{40}", before) is None
            or after != environment["GITHUB_SHA"] or request.get("audited_commit") != before
            or request.get("report_date") != report_time.astimezone(BJ).date().isoformat()
            or report_time.astimezone(BJ).date() != now.astimezone(BJ).date()
            or not isinstance(request.get("request_id"), str)
            or re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9-]{7,100}", request["request_id"]) is None):
        raise DeliveryUnknown("No current, reviewed replay request")
    compare = api(REPOSITORY, f"compare/{before}...{after}")
    if (compare.get("status") not in ("ahead", "identical")
            or [item["filename"] for item in compare.get("files", [])] != [REPLAY_PATH]):
        raise DeliveryUnknown("Replay push must change only the reviewed request, not report code")
    return "replay:" + request["request_id"]


def actual_send(metadata_path, receipt_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import send_report
    original = sys.argv
    try:
        sys.argv = ["send_report.py", str(metadata_path), "--receipt", str(receipt_path)]
        if send_report.main() != 0:
            raise DeliveryUnknown("Mailer did not confirm acceptance")
    finally:
        sys.argv = original


def deliver(metadata_path, *, environment=None, now=None, journal=None, history=None, send=None, preview=False):
    transaction_start = time.monotonic()
    environment = os.environ if environment is None else environment
    now = now or datetime.now(timezone.utc)
    metadata_path = Path(metadata_path)
    raw = metadata_path.read_bytes()
    meta = json.loads(raw)
    report_raw = metadata_path.with_name("report.json").read_bytes()
    verify_report_binding(meta, report_raw)
    report = json.loads(report_raw)
    if preview:
        return {"status": "preview", "sent": False}
    if (environment.get("GITHUB_REPOSITORY") != REPOSITORY or environment.get("GITHUB_REF") != "refs/heads/main"
            or environment.get("GITHUB_EVENT_NAME") not in ("schedule", "workflow_dispatch", "push")
            or not re.fullmatch(r"[0-9a-f]{40}", environment.get("GITHUB_SHA", ""))
            or not all(str(environment.get(key, "")).isdigit() for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"))):
        raise DeliveryUnknown("Only the trusted main-branch weekly workflow may send")
    generated = stamp(report["generated_at_utc"])
    if not 0 <= (now - generated).total_seconds() <= 7 * 86400:
        raise DeliveryUnknown("Current report is stale or future-dated")
    identity = {"repository": REPOSITORY, "iso_week": list(generated.astimezone(BJ).isocalendar()[:2]),
                "envelope": envelope(environment), "namespace": replay_namespace(environment, generated, now)}
    key = digest(identity)
    journal = journal or GitJournal()
    existing = journal.read(key)
    if existing:
        state = existing[1]
        if (state.get("identity") == identity and state.get("phase") == "smtp_accepted"
                and state.get("receipt", {}).get("status") == "smtp_accepted"
                and state["receipt"].get("metadata_sha256") == state.get("metadata_sha256")):
            return {"status": "already_sent", "sent": False, "key": key}
        raise DeliveryUnknown("Delivery was claimed earlier; outcome uncertain, automatic resend prohibited")
    if identity["namespace"] == "normal":
        prior = (history or historical_delivery)(identity, now, environment)
        if prior.get("status") == "sent":
            return {**prior, "status": "already_sent_legacy", "sent": False, "key": key}
        if prior.get("status") != "not_sent":
            raise DeliveryUnknown("Historical send outcome uncertain; automatic resend prohibited")
    if send is None:
        if any(not environment.get(k) for k in ("MAIL_SERVER", "MAIL_PORT", "MAIL_USERNAME", "MAIL_PASSWORD")):
            raise DeliveryUnknown("Mail configuration incomplete; no delivery claim was created")
        try:
            if not 1 <= int(environment["MAIL_PORT"]) <= 65535:
                raise ValueError("port")
        except (ValueError, TypeError):
            raise DeliveryUnknown("Invalid mail port; no delivery claim was created") from None
    state = {"schema_version": 1, "key": key, "identity": identity, "owner": uuid.uuid4().hex,
             "phase": "claimed", "metadata_sha256": hashlib.sha256(raw).hexdigest(),
             "report_sha256": meta["report_sha256"], "run_id": environment["GITHUB_RUN_ID"],
             "run_attempt": environment["GITHUB_RUN_ATTEMPT"], "claimed_at": now.isoformat()}
    claim_sha = journal.claim(key, state)
    receipt_path = metadata_path.with_name("delivery-receipt.json")
    if receipt_path.exists():
        raise DeliveryUnknown("Unexpected existing local receipt; persistent claim retained, do not resend")
    # Nothing after this point may cause the same identity to be automatically sent again.
    try:
        (send or actual_send)(metadata_path, receipt_path)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        raise DeliveryUnknown("SMTP outcome is uncertain; persistent claim retained, do not resend") from None
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    accepted = stamp(receipt.get("accepted_at"))
    deadline = now + timedelta(seconds=max(0, time.monotonic() - transaction_start) + 1)
    if (receipt.get("schema_version") != 1 or not now.replace(microsecond=0) <= accepted <= deadline
            or receipt.get("status") != "smtp_accepted" or receipt.get("metadata_sha256") != state["metadata_sha256"]
            or receipt.get("body_sha256") != hashlib.sha256(meta["body"].encode()).hexdigest()
            or receipt.get("subject_sha256") != hashlib.sha256(meta["subject"].encode()).hexdigest()
            or not re.fullmatch(r"<[^<>\s]+@[^<>\s]+>", receipt.get("message_id", ""))
            or envelope({"MAIL_FROM": receipt.get("sender"), "MAIL_TO": ",".join(receipt.get("recipients", []))}) != identity["envelope"]):
        raise DeliveryUnknown("Mailer receipt does not match the claimed delivery; do not resend")
    state = {**state, "phase": "smtp_accepted", "receipt": receipt}
    journal.finish(key, claim_sha, state)
    return {"status": "smtp_accepted", "sent": True, "key": key}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("metadata", type=Path)
    parser.add_argument("--preview", action="store_true")
    args = parser.parse_args()
    try:
        result = deliver(args.metadata, preview=args.preview)
        args.metadata.with_name("delivery-status.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (DeliveryUnknown, ApiFailure, ValueError, KeyError, OSError, subprocess.SubprocessError, zipfile.BadZipFile, zlib.error) as error:
        args.metadata.with_name("delivery-status.json").write_text(json.dumps({"status": "blocked_or_unknown", "sent": None, "error_type": type(error).__name__, "reason": str(error) if isinstance(error, (DeliveryUnknown, ApiFailure)) else "Delivery evidence invalid or unavailable; do not automatically resend"}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("Weekly delivery blocked or uncertain; see delivery-status.json. No automatic resend.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
