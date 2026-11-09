"""Independent delivery fault injection; no network or actual SMTP calls."""

import copy
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import io
import zipfile

import monitor
import weekly_delivery as delivery
import probe_delivery_store as storage_probe
from test_advisory import sample


class FakeJournal:
    def __init__(self):
        self.states = {}
        self.calls = []
        self.claim_unknown = False
        self.finish_unknown = False
        self.finish_applied = False
        self.read_unknown = False

    def read(self, key):
        self.calls.append(("read", key))
        if self.read_unknown:
            raise delivery.DeliveryUnknown("injected read uncertainty")
        return copy.deepcopy(self.states.get(key))

    def claim(self, key, state):
        self.calls.append(("claim", key))
        if key in self.states:
            raise delivery.DeliveryUnknown("already claimed")
        claim_sha = hashlib.sha1((key + state["owner"]).encode()).hexdigest()
        self.states[key] = (claim_sha, copy.deepcopy(state))
        if self.claim_unknown:
            raise delivery.DeliveryUnknown("claim may have committed")
        return claim_sha

    def finish(self, key, claim_sha, state):
        self.calls.append(("finish", key))
        if self.states[key][0] != claim_sha:
            raise delivery.DeliveryUnknown("owner changed")
        if self.finish_unknown and not self.finish_applied:
            raise delivery.DeliveryUnknown("receipt persistence uncertain")
        self.states[key] = (hashlib.sha1((claim_sha + "receipt").encode()).hexdigest(), copy.deepcopy(state))
        if self.finish_unknown:
            raise delivery.DeliveryUnknown("receipt committed but response lost")


class DeliveryAdversarialTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.now = datetime(2026, 11, 9, 8, 30, tzinfo=timezone.utc)
        self.environment = {
            "GITHUB_REPOSITORY": "liuruojiang/codex-daily-automation-probe",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_EVENT_NAME": "schedule",
            "GITHUB_RUN_ID": "10001",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_SHA": "a" * 40,
            "MAIL_FROM": "Audit User <audit@example.com>",
            "MAIL_TO": "Beta <b@example.com>, Alpha <a@example.com>",
        }
        self.journal = FakeJournal()
        self.send_calls = []
        self.history_calls = []

    def metadata(self, name="one", now=None, note="fixture"):
        now = now or self.now
        folder = self.root / name
        folder.mkdir()
        report = sample()
        report.update(generated_at_utc=now.isoformat(), method_version=monitor.METHOD_VERSION)
        report["warnings"] = [note]
        for item in report["core"].values():
            item["as_of"] = (now.date().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        for item in report["supplemental"].values():
            item["as_of"] = (now - timedelta(days=3)).date().isoformat()
        raw = json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8")
        (folder / "report.json").write_bytes(raw)
        metadata = {
            "subject": f"每周股票动量边界监控 | 偏边界 | {now.date()}",
            "body": monitor.render_text(report),
            "html_body": monitor.render_html(report),
            "attachment": None,
            "report_sha256": hashlib.sha256(raw).hexdigest(),
        }
        path = folder / "metadata.json"
        path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def history(self, identity, now, environment):
        self.history_calls.append(copy.deepcopy(identity))
        return {"status": "not_sent"}

    def send(self, metadata_path, receipt_path):
        self.assertTrue(any(state[1]["phase"] == "claimed" for state in self.journal.states.values()))
        self.send_calls.append(str(metadata_path))
        raw = Path(metadata_path).read_bytes()
        meta = json.loads(raw)
        receipt = {
            "schema_version": 1,
            "status": "smtp_accepted",
            "message_id": "<adversarial-mock@example.com>",
            "accepted_at": self.now.isoformat(),
            "metadata_sha256": hashlib.sha256(raw).hexdigest(),
            "subject_sha256": hashlib.sha256(meta["subject"].encode()).hexdigest(),
            "body_sha256": hashlib.sha256(meta["body"].encode()).hexdigest(),
            "sender": "audit@example.com",
            "recipients": ["b@example.com", "a@example.com"],
        }
        Path(receipt_path).write_text(json.dumps(receipt), encoding="utf-8")

    def invoke(self, path, **kwargs):
        arguments = dict(environment=self.environment, now=self.now, journal=self.journal,
                         history=self.history, send=self.send)
        arguments.update(kwargs)
        return delivery.deliver(path, **arguments)

    def assert_blocked(self, call):
        with self.assertRaises((delivery.DeliveryUnknown, ValueError, RuntimeError, OSError)):
            call()

    def test_preview_validates_binding_without_state_history_or_send(self):
        self.invoke(self.metadata(), preview=True)
        self.assertFalse(self.journal.calls)
        self.assertFalse(self.history_calls)
        self.assertFalse(self.send_calls)

    def test_preview_also_rejects_changed_report_bytes(self):
        path = self.metadata()
        with (path.parent / "report.json").open("ab") as handle:
            handle.write(b" ")
        self.assert_blocked(lambda: self.invoke(path, preview=True))
        self.assertFalse(self.journal.calls)
        self.assertFalse(self.send_calls)

    def test_feature_branch_cannot_claim_or_send(self):
        environment = dict(self.environment, GITHUB_REF="refs/heads/adversarial-test")
        self.assert_blocked(lambda: self.invoke(self.metadata(), environment=environment))
        self.assertFalse(self.journal.calls)
        self.assertFalse(self.history_calls)
        self.assertFalse(self.send_calls)

    def test_changed_body_does_not_bypass_normal_week_deduplication(self):
        self.invoke(self.metadata("first", note="first body"))
        self.invoke(self.metadata("second", note="different body"))
        self.assertEqual(len(self.send_calls), 1)
        self.assertEqual(len(self.journal.states), 1)

    def test_display_names_and_recipient_order_do_not_create_new_identity(self):
        self.invoke(self.metadata("first"))
        environment = dict(self.environment, MAIL_FROM="Different Name <audit@example.com>",
                           MAIL_TO="First <a@example.com>, Second <b@example.com>")
        self.invoke(self.metadata("second"), environment=environment)
        self.assertEqual(len(self.send_calls), 1)
        self.assertEqual(len(self.journal.states), 1)

    def test_iso_week_year_boundary_does_not_create_duplicate_week(self):
        last = datetime(2026, 12, 31, 8, tzinfo=timezone.utc)
        next_year = datetime(2027, 1, 1, 8, tzinfo=timezone.utc)
        self.now = last
        self.invoke(self.metadata("december", now=last), now=last)
        self.now = next_year
        self.invoke(self.metadata("january", now=next_year), now=next_year)
        self.assertEqual(len(self.send_calls), 1)
        self.assertEqual(len(self.journal.states), 1)

    def test_claim_commit_with_lost_response_does_not_send_or_retry_smtp(self):
        self.journal.claim_unknown = True
        self.assert_blocked(lambda: self.invoke(self.metadata("first")))
        self.journal.claim_unknown = False
        self.assert_blocked(lambda: self.invoke(self.metadata("second")))
        self.assertEqual(len(self.send_calls), 0)
        self.assertEqual(len(self.journal.states), 1)

    def test_smtp_uncertainty_keeps_claim_and_blocks_clean_runner_retry(self):
        calls = []
        def uncertain_send(metadata_path, receipt_path):
            calls.append(str(metadata_path))
            raise OSError("SMTP outcome unknown")
        self.assert_blocked(lambda: self.invoke(self.metadata("first"), send=uncertain_send))
        self.assert_blocked(lambda: self.invoke(self.metadata("second"), send=uncertain_send))
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(self.journal.states), 1)

    def test_post_smtp_receipt_persistence_failure_retains_evidence_and_blocks_retry(self):
        self.journal.finish_unknown = True
        first = self.metadata("first")
        self.assert_blocked(lambda: self.invoke(first))
        self.assertTrue((first.parent / "delivery-receipt.json").exists())
        self.journal.finish_unknown = False
        self.assert_blocked(lambda: self.invoke(self.metadata("second")))
        self.assertEqual(len(self.send_calls), 1)

    def test_completed_receipt_with_lost_finalize_response_never_sends_again(self):
        self.journal.finish_unknown = True
        self.journal.finish_applied = True
        self.assert_blocked(lambda: self.invoke(self.metadata("first")))
        self.journal.finish_unknown = False
        self.invoke(self.metadata("second"))
        self.assertEqual(len(self.send_calls), 1)

    def test_legacy_sent_history_prevents_new_claim_and_send(self):
        self.invoke(self.metadata(), history=lambda *args: {"status": "sent", "run_id": 999})
        self.assertFalse(self.send_calls)
        self.assertFalse(any(call[0] == "claim" for call in self.journal.calls))

    def test_unknown_history_or_journal_is_not_absence(self):
        self.assert_blocked(lambda: self.invoke(self.metadata("history"), history=lambda *args: {"status": "unknown"}))
        self.journal.read_unknown = True
        self.assert_blocked(lambda: self.invoke(self.metadata("journal")))
        self.assertFalse(self.send_calls)
        self.assertFalse(any(call[0] == "claim" for call in self.journal.calls))

    def test_existing_claim_cannot_be_reentered_by_same_run_id(self):
        self.journal.claim_unknown = True
        self.assert_blocked(lambda: self.invoke(self.metadata("one")))
        self.journal.claim_unknown = False
        self.assert_blocked(lambda: self.invoke(self.metadata("two")))
        self.assertFalse(self.send_calls)

    def test_explicit_replay_has_its_own_once_only_identity(self):
        environment = dict(self.environment, GITHUB_EVENT_NAME="push")
        with patch.object(delivery, "replay_namespace", return_value="replay:reviewed-request-0001"):
            self.invoke(self.metadata("first"), environment=environment)
            self.invoke(self.metadata("second"), environment=environment)
        self.assertEqual(len(self.send_calls), 1)
        self.assertFalse(self.history_calls)

    def test_wrong_receipt_identity_cannot_finalize_claim(self):
        def wrong_sender(path, receipt_path):
            self.send(path, receipt_path)
            receipt = json.loads(Path(receipt_path).read_text(encoding="utf8"))
            receipt["sender"] = "wrong@example.com"
            Path(receipt_path).write_text(json.dumps(receipt), encoding="utf8")
        self.assert_blocked(lambda: self.invoke(self.metadata(), send=wrong_sender))
        self.assertEqual(next(iter(self.journal.states.values()))[1]["phase"], "claimed")

    def test_invalid_receipt_timestamp_cannot_be_persisted_as_confirmed_send(self):
        def future_receipt(path, receipt_path):
            self.send(path, receipt_path)
            receipt = json.loads(Path(receipt_path).read_text(encoding="utf8"))
            receipt["accepted_at"] = "2999-01-01T00:00:00+00:00"
            Path(receipt_path).write_text(json.dumps(receipt), encoding="utf8")
        self.assert_blocked(lambda: self.invoke(self.metadata(), send=future_receipt))
        self.assertEqual(next(iter(self.journal.states.values()))[1]["phase"], "claimed")


class FakeGitRemote:
    """A deterministic Git object store with atomic create and non-forced fast-forward."""
    def __init__(self):
        self.blobs, self.trees, self.commits, self.refs = {}, {}, {}, {}
        self.calls = []
        self.lost_create = False
        self.failed_create = False
        self.lost_finish = False
        self.failed_finish = False

    def request(self, path, method="GET", payload=None):
        self.calls.append((method, path, copy.deepcopy(payload)))
        if method == "POST" and path == "git/blobs":
            raw = payload["content"].encode()
            sha = hashlib.sha1(raw).hexdigest()
            self.blobs[sha] = raw
            return {"sha": sha}
        if method == "POST" and path == "git/trees":
            sha = hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            self.trees[sha] = copy.deepcopy(payload["tree"])
            return {"sha": sha}
        if method == "POST" and path == "git/commits":
            sha = hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            self.commits[sha] = {"tree": {"sha": payload["tree"]}, "parents": payload["parents"]}
            return {"sha": sha}
        if method == "POST" and path == "git/refs":
            ref = payload["ref"].removeprefix("refs/")
            if ref in self.refs:
                raise delivery.ApiFailure(422)
            if self.failed_create:
                raise delivery.ApiFailure()
            self.refs[ref] = payload["sha"]
            if self.lost_create:
                raise delivery.ApiFailure()
            return {"ref": payload["ref"], "object": {"sha": payload["sha"]}}
        if method == "PATCH" and path.startswith("git/refs/"):
            ref = path.removeprefix("git/refs/")
            if self.failed_finish:
                raise delivery.ApiFailure()
            if payload["force"] is not False or self.commits[payload["sha"]]["parents"] != [self.refs[ref]]:
                raise delivery.ApiFailure(422)
            self.refs[ref] = payload["sha"]
            if self.lost_finish:
                raise delivery.ApiFailure()
            return {"object": {"sha": payload["sha"]}}
        if method == "GET" and path.startswith("git/ref/"):
            ref = path.removeprefix("git/ref/")
            if ref not in self.refs:
                raise delivery.ApiFailure(404)
            return {"ref": "refs/" + ref, "object": {"type": "commit", "sha": self.refs[ref]}}
        if method == "GET" and path.startswith("git/commits/"):
            return copy.deepcopy(self.commits[path.removeprefix("git/commits/")])
        if method == "GET" and path.startswith("git/trees/"):
            return {"tree": copy.deepcopy(self.trees[path.removeprefix("git/trees/")])}
        if method == "GET" and path.startswith("git/blobs/"):
            return {"content": base64.b64encode(self.blobs[path.removeprefix("git/blobs/")]).decode()}
        raise AssertionError((method, path))


class GitJournalAdversarialTests(unittest.TestCase):
    def setUp(self):
        self.remote = FakeGitRemote()
        self.journal = delivery.GitJournal()
        self.journal.request = self.remote.request
        self.key = "a" * 64
        self.state = {"schema_version": 1, "key": self.key, "owner": "owner-one", "phase": "claimed"}

    def test_lost_create_response_can_continue_only_after_exact_owner_readback(self):
        self.remote.lost_create = True
        sha = self.journal.claim(self.key, self.state)
        self.assertEqual(self.journal.read(self.key), (sha, self.state))
        foreign = dict(self.state, owner="owner-two")
        with self.assertRaises(delivery.DeliveryUnknown):
            self.journal.claim(self.key, foreign)
        self.assertEqual(self.journal.read(self.key)[1]["owner"], "owner-one")

    def test_unconfirmed_create_is_blocked(self):
        self.remote.failed_create = True
        with self.assertRaises(delivery.DeliveryUnknown):
            self.journal.claim(self.key, self.state)
        self.assertFalse(self.remote.refs)

    def test_lost_finalization_response_is_confirmed_without_forcing_ref(self):
        sha = self.journal.claim(self.key, self.state)
        accepted = dict(self.state, phase="smtp_accepted", receipt={"status": "smtp_accepted"})
        self.remote.lost_finish = True
        self.journal.finish(self.key, sha, accepted)
        self.assertEqual(self.journal.read(self.key)[1], accepted)
        patches = [payload for method, path, payload in self.remote.calls if method == "PATCH"]
        self.assertEqual([payload["force"] for payload in patches], [False])

    def test_unconfirmed_finalization_leaves_claim_for_manual_reconciliation(self):
        sha = self.journal.claim(self.key, self.state)
        self.remote.failed_finish = True
        with self.assertRaises(delivery.DeliveryUnknown):
            self.journal.finish(self.key, sha, dict(self.state, phase="smtp_accepted"))
        self.assertEqual(self.journal.read(self.key), (sha, self.state))

    def test_a_competing_completion_is_never_force_overwritten(self):
        sha = self.journal.claim(self.key, self.state)
        other = dict(self.state, phase="smtp_accepted", receipt={"message_id": "other"})
        self.journal.finish(self.key, sha, other)
        with self.assertRaises(delivery.DeliveryUnknown):
            self.journal.finish(self.key, sha, dict(other, receipt={"message_id": "ours"}))
        self.assertEqual(self.journal.read(self.key)[1], other)

    def test_non_object_transaction_json_is_unknown_not_an_uncaught_shape_error(self):
        self.journal.claim(self.key, self.state)
        sha = self.remote.refs[delivery.REF_PREFIX + self.key]
        tree = self.remote.trees[self.remote.commits[sha]["tree"]["sha"]]
        self.remote.blobs[tree[0]["sha"]] = b"[]"
        with self.assertRaises(delivery.DeliveryUnknown):
            self.journal.read(self.key)


class HistoricalDeliveryAdversarialTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 9, 9, tzinfo=timezone.utc)
        self.environment = {"GITHUB_RUN_ID": "100", "GITHUB_RUN_ATTEMPT": "2"}
        self.identity = {"iso_week": [2026, 46], "envelope": {"sender": "audit@example.com", "recipients": ["a@example.com"]}}

    def archive(self, generated=None, accepted=None):
        generated = generated or self.now
        accepted = accepted or self.now
        meta = {"subject": f"每周股票动量边界监控 | 偏边界 | {generated.date()}",
                "body": f"生成时间（UTC）：{generated.isoformat()}\nFixture", "html_body": "Fixture"}
        raw = json.dumps(meta).encode()
        receipt = {"status": "smtp_accepted", "message_id": "<mock@example.com>", "accepted_at": accepted.isoformat(),
                   "metadata_sha256": hashlib.sha256(raw).hexdigest(),
                   "subject_sha256": hashlib.sha256(meta["subject"].encode()).hexdigest(),
                   "body_sha256": hashlib.sha256(meta["body"].encode()).hexdigest(),
                   "sender": "audit@example.com", "recipients": ["a@example.com"]}
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as archive:
            archive.writestr("metadata.json", raw)
            archive.writestr("delivery-receipt.json", json.dumps(receipt))
        return out.getvalue()

    def reconcile(self, jobs, archive=None, run_id=100):
        run = {"id": run_id, "head_branch": "main", "path": ".github/workflows/" + delivery.WORKFLOW, "conclusion": "failure"}
        def pages(repository, path, field):
            if field == "workflow_runs":
                return iter([run])
            if field == "jobs":
                return iter(jobs)
            if field == "artifacts":
                return iter([{"id": 77, "name": f"equity-momentum-boundary-{run_id}", "expired": False}] if archive else [])
            raise AssertionError(field)
        with patch.object(delivery, "pages", side_effect=pages), patch.object(delivery, "api", return_value=archive):
            return delivery.historical_delivery(self.identity, self.now, self.environment)

    def job(self, attempt, conclusion="in_progress"):
        return {"run_attempt": attempt, "steps": [{"name": "Send through the existing daily-report mailer",
                                                   "started_at": "2026-11-09T08:00:00Z", "conclusion": conclusion}]}

    def test_current_attempt_does_not_deadlock_its_own_first_send(self):
        self.assertEqual(self.reconcile([self.job(2)])["status"], "not_sent")

    def test_earlier_attempt_of_current_run_remains_uncertain_send_evidence(self):
        self.assertEqual(self.reconcile([self.job(1), self.job(2)])["status"], "unknown")

    def test_skipped_preview_send_is_not_legacy_uncertainty(self):
        self.assertEqual(self.reconcile([self.job(1, "skipped")], run_id=99)["status"], "not_sent")

    def test_failed_workflow_with_smtp_receipt_prevents_duplicate(self):
        result = self.reconcile([self.job(1, "success")], archive=self.archive(), run_id=99)
        self.assertEqual(result["status"], "sent")

    def test_beijing_week_is_not_inferred_from_utc_subject_date(self):
        # Sunday UTC 17:00 is Monday 01:00 Beijing, ISO week 46.
        generated = datetime(2026, 11, 8, 17, tzinfo=timezone.utc)
        self.assertTrue(delivery.accepted_archive(self.archive(generated=generated), self.identity, self.now))


class ReplayAuthorizationAdversarialTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 9, 9, tzinfo=timezone.utc)
        self.before, self.after = "a" * 40, "b" * 40
        self.environment = {"GITHUB_EVENT_NAME": "push", "GITHUB_EVENT_PATH": "fixture-event.json", "GITHUB_SHA": self.after}
        self.request = {"request_id": "reviewed-request-0001", "report_date": "2026-11-09", "audited_commit": self.before}
        self.event = {"before": self.before, "after": self.after}

    def invoke(self, request=None, files=None):
        request = request or self.request
        def read(path, *args, **kwargs):
            return json.dumps(self.event if path.name == "fixture-event.json" else request)
        compare = {"status": "ahead", "files": [{"filename": path} for path in (files or [delivery.REPLAY_PATH])]}
        with patch.object(Path, "read_text", autospec=True, side_effect=read), patch.object(delivery, "api", return_value=compare):
            return delivery.replay_namespace(self.environment, self.now, self.now)

    def test_current_request_only_push_is_explicit_replay(self):
        self.assertTrue(self.invoke().startswith("replay:"))

    def test_old_request_is_not_authority_for_current_report(self):
        with self.assertRaises(delivery.DeliveryUnknown):
            self.invoke(dict(self.request, report_date="2026-10-19"))

    def test_review_must_bind_exact_pre_push_code(self):
        with self.assertRaises(delivery.DeliveryUnknown):
            self.invoke(dict(self.request, audited_commit="c" * 40))

    def test_replay_push_cannot_also_change_report_code(self):
        with self.assertRaises(delivery.DeliveryUnknown):
            self.invoke(files=[delivery.REPLAY_PATH, "monitoring/equity_momentum_boundary/monitor.py"])


class IsolatedStorageProbeTests(unittest.TestCase):
    def setUp(self):
        self.environment = {"GITHUB_REPOSITORY": delivery.REPOSITORY, "GITHUB_EVENT_NAME": "workflow_dispatch",
                            "GITHUB_RUN_ID": "100", "GITHUB_RUN_ATTEMPT": "1"}
        self.journal = FakeJournal()

    def test_probe_persists_only_validation_namespace_and_is_idempotent(self):
        result = storage_probe.probe(self.environment, self.journal)
        second = storage_probe.probe(self.environment, self.journal)
        self.assertEqual(result, second)
        self.assertEqual(result["actual_emails_sent"], 0)
        self.assertEqual(len(self.journal.states), 1)
        state = next(iter(self.journal.states.values()))[1]
        self.assertEqual(state["identity"]["namespace"], "validation-only-no-mail")
        self.assertEqual(state["phase"], "validation_complete")
        self.assertNotIn("receipt", state)
        self.assertEqual(sum(call[0] == "claim" for call in self.journal.calls), 1)
        normal = {"repository": delivery.REPOSITORY, "iso_week": [2026, 46],
                  "envelope": {"sender": "audit@example.com", "recipients": ["a@example.com"]}, "namespace": "normal"}
        self.assertNotEqual(result["key"], delivery.digest(normal))

    def test_probe_refuses_automatic_event_and_foreign_repository_before_writes(self):
        for change in ({"GITHUB_EVENT_NAME": "schedule"}, {"GITHUB_REPOSITORY": "foreign/repo"}):
            with self.assertRaises(ValueError):
                storage_probe.probe(dict(self.environment, **change), self.journal)
        self.assertFalse(self.journal.calls)

    def test_probe_workflow_is_explicitly_opt_in_and_separate_from_mail(self):
        path = Path(__file__).resolve().parents[2] / ".github/workflows/equity-momentum-boundary-weekly.yml"
        workflow = path.read_text(encoding="utf8")
        probe_step = workflow.split("- name: Verify isolated no-mail delivery storage", 1)[1].split("- name:", 1)[0]
        self.assertIn("inputs.send_email == false && inputs.verify_delivery_store == true", probe_step)
        self.assertIn("github.event_name == 'workflow_dispatch'", probe_step)
        self.assertIn("probe_delivery_store.py", probe_step)
        self.assertNotIn("MAIL_PASSWORD", probe_step)
        configuration = workflow.split("verify_delivery_store:", 1)[1].split("permissions:", 1)[0]
        self.assertIn("default: false", configuration)


if __name__ == "__main__":
    unittest.main()
