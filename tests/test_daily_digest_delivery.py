"""Real gate counterexamples: accepted mail, uncertain SMTP and duplicate schedulers."""
import hashlib
import importlib.util
import json
import urllib.request
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("daily_gate", ROOT / "scripts/check_daily_digest_delivery.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
REPO = "liuruojiang/codex-daily-automation-probe"


def metadata(day="2026-11-03", family="etf"):
    return {"subject": gate.FAMILIES[family][2] + day, "body": "real report body boundary", "attachment": None}


def run_record(**updates):
    value = {"id": 7, "path": ".github/workflows/etf-allocation-digest.yml", "head_branch": "main",
             "event": "schedule", "repository": {"full_name": REPO}, "head_repository": {"full_name": REPO},
             "head_sha": "a" * 40, "created_at": "2026-11-02T21:30:00Z", "updated_at": "2026-11-02T21:32:00Z"}
    value.update(updates)
    return value


def evidence(meta=None, accepted=False):
    meta = metadata() if meta is None else meta
    raw = json.dumps(meta).encode()
    values = {"metadata.json": (raw, meta)}
    if accepted:
        receipt = {"schema_version": 1, "status": "smtp_accepted", "message_id": "<accepted@example.org>",
                   "accepted_at": "2026-11-02T21:31:00+00:00", "metadata_sha256": hashlib.sha256(raw).hexdigest(),
                   "subject_sha256": hashlib.sha256(meta["subject"].encode()).hexdigest(),
                   "body_sha256": hashlib.sha256(meta["body"].encode()).hexdigest()}
        values["delivery_receipt.json"] = (b"", receipt)
    return values


class API:
    def __init__(self, run=None, conclusion="success", artifacts=True, started=True):
        self.run = run or run_record()
        self.conclusion, self.artifacts, self.started = conclusion, artifacts, started

    def pages(self, path, key):
        if key == "workflow_runs":
            return iter([self.run])
        if key == "jobs":
            return iter([{"name": "send", "steps": [{"name": "Send Gmail", "conclusion": self.conclusion,
                         "started_at": "2026-11-02T21:31:00Z" if self.started else None}]}])
        if key == "artifacts":
            return iter([{"id": 3, "name": "us-etf-allocation-digest", "expired": False,
                          "workflow_run": {"id": 7, "head_sha": "a" * 40, "head_branch": "main"}}] if self.artifacts else [])
        raise AssertionError(path)


def test_success_suppresses_native_fallback_even_after_later_job_failure(monkeypatch):
    monkeypatch.setattr(gate, "read_artifact", lambda *_: evidence())
    result = gate.reconcile(API(run_record(conclusion="failure")), REPO, "etf", metadata())
    assert result == {"should_send": "false", "delivery_state": "sent_confirmed", "prior_run_id": "7"}


def test_accepted_receipt_recovers_failed_send_step(monkeypatch):
    monkeypatch.setattr(gate, "read_artifact", lambda *_: evidence(accepted=True))
    assert gate.reconcile(API(conclusion="failure"), REPO, "etf", metadata())["delivery_state"] == "sent_confirmed"


@pytest.mark.parametrize("field", ["body_sha256", "metadata_sha256", "subject_sha256", "accepted_at", "message_id"])
def test_tampered_receipt_cannot_authorize_resend_or_claim_success(monkeypatch, field):
    values = evidence(accepted=True)
    values["delivery_receipt.json"][1][field] = "tampered"
    monkeypatch.setattr(gate, "read_artifact", lambda *_: values)
    with pytest.raises(RuntimeError, match="delivery_unknown"):
        gate.reconcile(API(conclusion="failure"), REPO, "etf", metadata())


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", None])
def test_smtp_interruption_is_unknown_and_never_blindly_retried(monkeypatch, conclusion):
    monkeypatch.setattr(gate, "read_artifact", lambda *_: evidence())
    with pytest.raises(RuntimeError, match="delivery_unknown"):
        gate.reconcile(API(conclusion=conclusion), REPO, "etf", metadata())


def test_missing_post_send_artifact_is_unknown():
    with pytest.raises(RuntimeError, match="delivery_unknown"):
        gate.reconcile(API(artifacts=False), REPO, "etf", metadata())


def test_failure_before_send_can_be_retried():
    assert gate.reconcile(API(started=False), REPO, "etf", metadata())["should_send"] == "true"


def test_other_report_date_does_not_suppress_today(monkeypatch):
    monkeypatch.setattr(gate, "read_artifact", lambda *_: evidence(metadata("2026-11-02")))
    assert gate.reconcile(API(), REPO, "etf", metadata())["should_send"] == "true"


@pytest.mark.parametrize("old_conclusion", ["success", "failure"])
def test_rerun_never_mixes_send_steps_and_another_attempt_body(monkeypatch, old_conclusion):
    monkeypatch.setattr(gate, "read_artifact", lambda *_: evidence(metadata("2026-11-02")))
    with pytest.raises(RuntimeError, match="mixed-attempt"):
        gate.reconcile(API(run_record(run_attempt=2), conclusion=old_conclusion), REPO, "etf", metadata())


def intent_values(day):
    values = evidence(metadata(day))
    intent = {"schema_version": 1, "status": "send_intent", "family": "etf", "report_date": day,
              "run_id": "7", "run_attempt": "1", "head_sha": "a" * 40,
              "workflow": "etf-allocation-digest.yml", "metadata_sha256": hashlib.sha256(values["metadata.json"][0]).hexdigest()}
    values["send_intent.json"] = (b"", intent)
    return values


def intent_only_api():
    api = API(conclusion="failure")
    original = api.pages
    def pages(path, key):
        rows = list(original(path, key))
        if key == "artifacts":
            rows[0]["name"] = "etf-send-intent-7-1"
        return iter(rows)
    api.pages = pages
    return api


def test_bound_intent_excludes_prior_day_when_final_upload_missing(monkeypatch):
    monkeypatch.setattr(gate, "read_artifact", lambda *_: intent_values("2026-11-02"))
    assert gate.reconcile(intent_only_api(), REPO, "etf", metadata())["should_send"] == "true"


def test_matching_day_intent_without_receipt_remains_unknown(monkeypatch):
    monkeypatch.setattr(gate, "read_artifact", lambda *_: intent_values("2026-11-03"))
    with pytest.raises(RuntimeError, match="delivery_unknown"):
        gate.reconcile(intent_only_api(), REPO, "etf", metadata())


@pytest.mark.parametrize("field", ["run_id", "run_attempt", "head_sha", "workflow", "metadata_sha256", "report_date", "family"])
def test_intent_requires_source_attempt_date_and_hash_binding(monkeypatch, field):
    values = intent_values("2026-11-02")
    values["send_intent.json"][1][field] = "tampered"
    monkeypatch.setattr(gate, "read_artifact", lambda *_: values)
    with pytest.raises(RuntimeError, match="identity/hash mismatch"):
        gate.reconcile(intent_only_api(), REPO, "etf", metadata())


def test_wrong_source_is_blocked():
    with pytest.raises(RuntimeError, match="identity"):
        gate.reconcile(API(run_record(head_repository={"full_name": "untrusted/fork"})), REPO, "etf", metadata())


def test_wrong_artifact_head_is_blocked(monkeypatch):
    api = API()
    original = api.pages
    def pages(path, key):
        rows = list(original(path, key))
        if key == "artifacts":
            rows[0]["workflow_run"]["head_sha"] = "b" * 40
        return iter(rows)
    api.pages = pages
    with pytest.raises(RuntimeError, match="identity mismatch"):
        gate.reconcile(api, REPO, "etf", metadata())


def test_artifact_redirect_does_not_forward_github_token():
    request = urllib.request.Request("https://api.github.com/artifact", headers={"Authorization": "Bearer test-only"})
    result = gate.SafeArtifactRedirect().redirect_request(request, None, 302, "Found", {}, "https://storage.example.org/signed")
    assert result.get_header("Authorization") is None


@pytest.mark.parametrize("family", gate.FAMILIES)
def test_report_identity_requires_exact_subject_body_date(family):
    assert gate.report_day(metadata(family=family), family).isoformat() == "2026-11-03"
    with pytest.raises(ValueError):
        gate.report_day({**metadata(family=family), "subject": "wrong"}, family)


@pytest.mark.parametrize("file,cron,family", [
    ("etf-allocation-digest.yml", "0 22 * * *", "etf"),
    ("ai-hot-digest.yml", "30 1 * * *", "ai-hot"),
])
def test_workflows_serialize_and_preserve_no_send_validation(file, cron, family):
    workflow = yaml.load((ROOT / ".github/workflows" / file).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert workflow["on"]["schedule"] == [{"cron": cron}]
    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    assert workflow["permissions"]["actions"] == "read"
    steps = workflow["jobs"]["send"]["steps"]
    send = next(s for s in steps if s.get("name") == "Send Gmail")
    assert send["if"] == "steps.delivery_gate.outputs.should_send == 'true'"
    assert "--receipt artifacts/delivery_receipt.json" in send["run"]
    gate_step = next(s for s in steps if s.get("id") == "delivery_gate")
    assert f"--family {family}" in gate_step["run"] and "--validation-only" in gate_step["run"]
    intent = next(s for s in steps if s.get("name") == "Preserve send intent before SMTP")
    assert intent["if"] == send["if"]
    assert steps.index(intent) < steps.index(send)
    if family == "etf":
        history = next(s for s in steps if s.get("name") == "Persist digest history")
        assert history["if"] == send["if"]
