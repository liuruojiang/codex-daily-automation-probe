"""Fault boundaries use mocked SMTP/GitHub only; no message is sent."""
import base64
import copy
import hashlib
import io
import json
import os
import shutil
import subprocess
from pathlib import Path
import sys
import urllib.error
import urllib.request
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import etf_delivery_state as state
import github_metadata
import send_report
import test_etf_preflight as preflight_test


@pytest.fixture
def identity(monkeypatch):
    values = {"GITHUB_REPOSITORY": "owner/repo", "DEFAULT_BRANCH": "main", "GITHUB_REF_NAME": "main",
              "GITHUB_EVENT_NAME": "schedule", "GITHUB_WORKFLOW_REF": "owner/repo/" + state.WORKFLOW + "@refs/heads/main",
              "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_SHA": "a" * 40,
              "MAIL_FROM": "sender@example.invalid", "MAIL_TO": "recipient@example.invalid", "GH_TOKEN": "synthetic-token"}
    for key, value in values.items():monkeypatch.setenv(key, value)
    return values


class MemoryGitHub:
    def __init__(self):
        self.files = {};self.calls = [];self.version = 0
        self.fail_create = False;self.fail_finish = False
        self.activation = {"schema_version": state.BASELINE_SCHEMA, "state": "approved", "repository": "owner/repo",
            "branch": "main", "workflow_path": state.WORKFLOW, "first_report_date": "2026-10-21",
            "legacy_coverage_through": "2026-10-20", "reviewed_at": "2026-10-20T00:00:00Z",
            "legacy_sender_stopped": True, "legacy_evidence": "synthetic offline legacy run review"}
    def __call__(self, url, token, *, method="GET", data=None):
        self.calls.append((method, url))
        path = url.split("?")[0]
        if method == "GET":
            if path.endswith("/activation.json") and self.activation is not None:
                return 200, {"encoding": "base64", "content": base64.b64encode(json.dumps(self.activation).encode()).decode()}
            if path not in self.files:raise urllib.error.HTTPError(url, 404, "synthetic missing", {}, None)
            return 200, self.files[path]
        if "sha" not in data:
            if self.fail_create:raise TimeoutError("synthetic unknown claim")
            if path in self.files:raise urllib.error.HTTPError(url, 409, "synthetic conflict", {}, None)
        else:
            if self.fail_finish:raise TimeoutError("synthetic unknown update")
            if self.files[path]["sha"] != data["sha"]:raise urllib.error.HTTPError(url, 409, "synthetic conflict", {}, None)
        self.version += 1;sha = str(self.version)
        value = json.loads(base64.b64decode(data["content"]))
        self.files[path] = {"sha": sha, "value": value}
        return (200 if "sha" in data else 201), {"content": {"sha": sha}}


def metadata(path, body="synthetic source only"):
    path.write_text(json.dumps({"subject": "美股 ETF 与资产配置日报 - 2026-10-21", "body": body}, ensure_ascii=False), encoding="utf-8")
    return path


def invoke(path, receipt, monkeypatch, *, durable=True):
    args = ["send_report.py", str(path), "--receipt", str(receipt)]
    if durable:args += ["--durable-github-etf"]
    monkeypatch.setattr(sys, "argv", args)
    return send_report.main()


def install_transport(monkeypatch, transport):
    original = state.GitHubLedger
    monkeypatch.setattr(send_report, "GitHubLedger", lambda intent, token: original(intent, token, transport))


def accepted_sender(calls):
    def send(*args, **kwargs):
        calls.append(kwargs["message_id"])
        return kwargs["message_id"]
    return send


def test_durable_claim_is_committed_before_smtp_and_not_a_message_id_dedup_assumption(tmp_path, monkeypatch, identity):
    remote = MemoryGitHub();install_transport(monkeypatch, remote);calls = []
    def send(*args, **kwargs):
        assert len(remote.files) == 1
        assert next(iter(remote.files.values()))["value"]["state"] == "in_flight"
        calls.append(kwargs["message_id"]);return kwargs["message_id"]
    monkeypatch.setattr(send_report, "send_mail", send)
    path = metadata(tmp_path / "metadata.json");receipt = tmp_path / "accepted.json"
    assert invoke(path, receipt, monkeypatch) == 0
    saved = json.loads(receipt.read_text(encoding="utf-8"))
    assert saved["message_id"] == calls[0] and saved["source_run_id"] == "123"
    assert next(iter(remote.files.values()))["value"]["state"] == "smtp_accepted"
    assert "synthetic-token" not in json.dumps(remote.files)
    assert "recipient@example.invalid" not in json.dumps(remote.files)


def test_new_runner_and_new_run_cannot_resend_even_when_same_day_body_changes(tmp_path, monkeypatch, identity):
    remote = MemoryGitHub();install_transport(monkeypatch, remote);calls = []
    monkeypatch.setattr(send_report, "send_mail", accepted_sender(calls))
    path = metadata(tmp_path / "metadata.json")
    invoke(path, tmp_path / "first.json", monkeypatch)
    monkeypatch.setenv("GITHUB_RUN_ID", "456");metadata(path, "changed same-day rebuild")
    with pytest.raises(state.DeliveryBlocked, match="already exists"):
        invoke(path, tmp_path / "another-runner.json", monkeypatch)
    assert len(calls) == 1


@pytest.mark.parametrize("fault", ["smtp_unknown", "receipt_disk_failure", "completion_api_failure", "process_death"])
def test_every_post_claim_uncertain_boundary_blocks_new_runner_retry(tmp_path, monkeypatch, identity, fault):
    remote = MemoryGitHub();install_transport(monkeypatch, remote);calls = []
    def send(*args, **kwargs):
        calls.append(kwargs["message_id"])
        if fault == "smtp_unknown":raise TimeoutError("synthetic DATA response lost")
        if fault == "process_death":raise KeyboardInterrupt()
        return kwargs["message_id"]
    monkeypatch.setattr(send_report, "send_mail", send)
    path = metadata(tmp_path / "metadata.json")
    original_write = send_report.write_receipt
    if fault == "receipt_disk_failure":
        def fail_receipt(path, value):
            if path.name == "accepted.json":raise OSError("synthetic accepted receipt disk failure")
            return original_write(path, value)
        monkeypatch.setattr(send_report, "write_receipt", fail_receipt)
    if fault == "completion_api_failure":remote.fail_finish = True
    with pytest.raises((TimeoutError, OSError, KeyboardInterrupt)):
        invoke(path, tmp_path / "accepted.json", monkeypatch)
    with pytest.raises(state.DeliveryBlocked, match="already exists"):
        invoke(path, tmp_path / "new-runner.json", monkeypatch)
    assert len(calls) == 1


def test_unknown_claim_result_prevents_any_smtp(tmp_path, monkeypatch, identity):
    remote = MemoryGitHub();remote.fail_create = True;install_transport(monkeypatch, remote)
    path = metadata(tmp_path / "metadata.json")
    with patch.object(send_report, "send_mail") as send:
        with pytest.raises(state.DeliveryBlocked, match="SMTP was not attempted"):
            invoke(path, tmp_path / "accepted.json", monkeypatch)
    send.assert_not_called()


@pytest.mark.parametrize("change", [None, {"state": "draft"}, {"legacy_sender_stopped": False},
    {"first_report_date": "2026-10-22", "legacy_coverage_through": "2026-10-21"},
    {"legacy_coverage_through": "2026-10-19"}, {"reviewed_at": "2026-10-21T00:00:00Z"},
    {"repository": "other/repo"}, {"workflow_path": "other.yml"}, {"legacy_evidence": ""}])
def test_missing_unreviewed_or_historical_activation_never_claims_or_sends(tmp_path, monkeypatch, identity, change):
    remote = MemoryGitHub()
    remote.activation = None if change is None else {**remote.activation, **change}
    install_transport(monkeypatch, remote)
    with patch.object(send_report, "send_mail") as send:
        with pytest.raises(state.DeliveryBlocked, match="activation"):
            invoke(metadata(tmp_path / "metadata.json"), tmp_path / "accepted.json", monkeypatch)
    send.assert_not_called()
    assert all(method == "GET" for method, _ in remote.calls)


def test_local_fsync_failure_is_before_any_remote_call_or_smtp(tmp_path, monkeypatch, identity):
    remote = MemoryGitHub();install_transport(monkeypatch, remote)
    with patch.object(send_report.os, "fsync", side_effect=OSError("synthetic fsync failure")), patch.object(send_report, "send_mail") as send:
        with pytest.raises(OSError, match="fsync"):
            invoke(metadata(tmp_path / "metadata.json"), tmp_path / "accepted.json", monkeypatch)
    send.assert_not_called();assert remote.calls == []


def test_claim_conflict_never_sends(tmp_path, monkeypatch, identity):
    remote = MemoryGitHub()
    def conflict(url, token, *, method="GET", data=None):
        if method == "PUT":raise urllib.error.HTTPError(url, 409, "synthetic racing create", {}, None)
        return remote(url, token, method=method, data=data)
    install_transport(monkeypatch, conflict)
    with patch.object(send_report, "send_mail") as send:
        with pytest.raises(state.DeliveryBlocked, match="SMTP was not attempted"):
            invoke(metadata(tmp_path / "metadata.json"), tmp_path / "accepted.json", monkeypatch)
    send.assert_not_called()


def test_local_receipt_failure_also_blocks_same_process_directory_rerun_without_remote(tmp_path, monkeypatch, identity):
    calls = [];monkeypatch.setattr(send_report, "send_mail", accepted_sender(calls))
    path = metadata(tmp_path / "metadata.json");receipt = tmp_path / "accepted.json"
    with patch.object(send_report, "write_receipt", side_effect=OSError("synthetic disk full")):
        with pytest.raises(OSError):invoke(path, receipt, monkeypatch, durable=False)
    assert not receipt.exists() and receipt.with_suffix(".intent.json").exists()
    with pytest.raises(FileExistsError):invoke(path, receipt, monkeypatch, durable=False)
    assert len(calls) == 1


@pytest.mark.parametrize("key,value", [("GITHUB_REF_NAME", "feature"), ("GITHUB_EVENT_NAME", "pull_request"),
                                      ("GITHUB_RUN_ID", ""), ("GITHUB_RUN_ATTEMPT", "0"),
                                      ("GITHUB_SHA", "invalid"), ("GITHUB_WORKFLOW_REF", "owner/repo/other.yml@refs/heads/main")])
def test_untrusted_sender_identity_never_calls_remote_or_smtp(tmp_path, monkeypatch, identity, key, value):
    monkeypatch.setenv(key, value);remote = MemoryGitHub();install_transport(monkeypatch, remote)
    with patch.object(send_report, "send_mail") as send:
        with pytest.raises(state.DeliveryBlocked):invoke(metadata(tmp_path / "metadata.json"), tmp_path / "accepted.json", monkeypatch)
    assert remote.calls == [];send.assert_not_called()


def api_run(conclusion="success", send="success"):
    run = {"id": 123, "head_sha": "a" * 40, "conclusion": conclusion}
    jobs = {"jobs": [{"run_id": 123, "steps": [{"name": "Send Gmail", "status": "completed", "conclusion": send}]}]}
    return run, jobs


def test_successful_send_is_not_undone_by_workflow_history_failure():
    run, jobs = api_run("failure")
    result = state.classify_delivery(run, jobs)
    assert result["status"] == "sent_confirmed" and result["post_send_failure"] is True
    assert result["workflow_conclusion"] == "failure"


def test_in_progress_workflow_is_not_mislabeled_as_post_send_failure():
    run, jobs = api_run(None)
    result = state.classify_delivery(run, jobs)
    assert result["status"] == "sent_confirmed" and result["post_send_failure"] is False


@pytest.mark.parametrize("send,expected", [("failure", "delivery_unknown"), ("cancelled", "delivery_unknown"),
                                           ("skipped", "not_sent"), (None, "unverified")])
def test_failed_or_skipped_send_is_not_upgraded_by_overall_success(send, expected):
    run, jobs = api_run("success", send)
    assert state.classify_delivery(run, jobs)["status"] == expected


def test_a_bound_acceptance_receipt_can_resolve_step_failure_but_wrong_source_cannot():
    run, jobs = api_run("failure", "failure")
    receipt = {"schema_version": 1, "status": "smtp_accepted", "source_run_id": "123", "source_head_sha": "a" * 40,
               "workflow_path": state.WORKFLOW, "message_id": "<synthetic@example.invalid>"}
    assert state.classify_delivery(run, jobs, receipt)["status"] == "sent_confirmed"
    for key in ("source_run_id", "source_head_sha", "workflow_path", "message_id", "schema_version"):
        altered = {**receipt, key: "other"}
        assert state.classify_delivery(run, jobs, altered)["status"] == "delivery_unknown"


def test_another_runs_jobs_cannot_prove_a_send():
    run, jobs = api_run();jobs["jobs"][0]["run_id"] = 999
    with pytest.raises(ValueError, match="belong"):state.classify_delivery(run, jobs)


def test_preflight_accepts_post_send_failure_without_changing_original_workflow_result():
    fixture = preflight_test.AdversarialPreflightTests();fixture.setUp();fixture.run["conclusion"] = "failure"
    assert fixture.check()["status"] == "PASS"
    assert fixture.run["conclusion"] == "failure"
    fixture.run["send_gmail"] = "failure"
    assert fixture.check()["status"] == "PARTIAL"


def test_preflight_receipt_must_bind_body_subject_run_and_head():
    fixture = preflight_test.AdversarialPreflightTests();fixture.setUp();fixture.run.update(conclusion="failure", send_gmail="failure")
    fixture.run["delivery_receipt"] = {"schema_version": 1, "status": "smtp_accepted", "source_run_id": "123",
        "source_head_sha": "abc", "workflow_path": state.WORKFLOW, "message_id": "<synthetic@example.invalid>",
        "body_sha256": hashlib.sha256(b"").hexdigest(),
        "subject_sha256": hashlib.sha256(fixture.meta["subject"].encode()).hexdigest()}
    assert fixture.check()["status"] == "PASS"
    original = copy.deepcopy(fixture.run["delivery_receipt"])
    for key in ("body_sha256", "subject_sha256", "source_run_id", "source_head_sha"):
        fixture.run["delivery_receipt"] = {**original, key: "mismatch"}
        assert fixture.check()["status"] == "PARTIAL"


def test_github_transport_rejects_redirect_without_copying_credentials():
    request = urllib.request.Request("https://api.github.com/repos/owner/repo", headers={"Authorization": "synthetic"})
    for target in ("https://other.example/", "https://api.github.com/moved"):
        with pytest.raises(ValueError, match="redirect refused"):
            github_metadata.NoRedirect().redirect_request(request, None, 302, "", {}, target)
    with pytest.raises(ValueError, match="fixed API origin"):
        github_metadata.get_json("https://other.example/repos/owner/repo", "synthetic")


def test_metadata_request_uses_only_the_bound_origin_and_synthetic_transport():
    calls = []
    class Response(io.BytesIO):
        status = 200
    def transport(req, timeout):
        calls.append(req);return Response(b'{"id":123}')
    assert github_metadata.get_json("https://api.github.com/repos/owner/repo/actions/runs/123", "synthetic", opener=transport) == {"id": 123}
    assert len(calls) == 1 and calls[0].get_header("Authorization") == "Bearer synthetic"


def test_workflow_history_rebase_preserves_pre_send_remote_ledger(tmp_path):
    if not shutil.which("git"):pytest.skip("git unavailable")
    def git(cwd, *args):
        result = subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args],
            cwd=cwd, capture_output=True, text=True)
        if os.name == "nt" and "couldn't create signal pipe, Win32 error 5" in result.stderr:
            pytest.skip("Managed Windows sandbox prevents Git shell signal pipe; Linux CI must cover rebase")
        result.check_returncode()
        return result.stdout
    remote = tmp_path / "remote.git";local = tmp_path / "runner";api = tmp_path / "api"
    git(tmp_path, "init", "--bare", str(remote));git(tmp_path, "clone", str(remote), str(local))
    for cwd in (local,):
        git(cwd, "config", "user.name", "Synthetic test");git(cwd, "config", "user.email", "test@example.invalid")
    git(local, "checkout", "-b", "main")
    history = local / "digest_history";history.mkdir();(history / "etf.json").write_text("[]\n")
    git(local, "add", "digest_history");git(local, "commit", "-m", "initial");git(local, "push", "-u", "origin", "main")
    git(tmp_path, "clone", "--branch", "main", str(remote), str(api))
    git(api, "config", "user.name", "Synthetic API");git(api, "config", "user.email", "api@example.invalid")
    ledger = api / "digest_history" / "etf_delivery";ledger.mkdir();(ledger / "claim.json").write_text('{"state":"in_flight"}\n')
    git(api, "add", "digest_history");git(api, "commit", "-m", "durable pre-send claim");git(api, "push")
    (history / "etf.json").write_text('[{"synthetic":"sent"}]\n')
    git(local, "add", "digest_history");git(local, "commit", "-m", "post-send history")
    git(local, "pull", "--rebase", "origin", "main");git(local, "push")
    assert json.loads((history / "etf_delivery" / "claim.json").read_text())["state"] == "in_flight"
    assert "synthetic" in git(tmp_path, "--git-dir", str(remote), "show", "main:digest_history/etf.json")
    assert "in_flight" in git(tmp_path, "--git-dir", str(remote), "show", "main:digest_history/etf_delivery/claim.json")
