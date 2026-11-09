"""Adversarial recovery regressions: isolate bad candidates, honor real attempts."""

from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import restore_previous as recovery
from monitor import METHOD_VERSION, render_html, render_text
from test_advisory import sample


NOW = datetime(2026, 11, 9, 8, 50, tzinfo=timezone.utc)


def bound_archive(generated="2026-11-02T07:32:25+00:00", bound=True, compression=zipfile.ZIP_STORED):
    report = sample()
    report.update(generated_at_utc=generated, method_version=METHOD_VERSION)
    raw = json.dumps(report).encode()
    body, html = render_text(report), render_html(report)
    meta = {"subject": "每周股票动量边界监控", "body": body, "html_body": html}
    if bound:
        meta["report_sha256"] = hashlib.sha256(raw).hexdigest()
    meta_raw = json.dumps(meta).encode()
    receipt = {"status": "smtp_accepted", "message_id": "<bound-test@example.com>",
               "accepted_at": (datetime.fromisoformat(generated) + timedelta(seconds=1)).isoformat(),
               "metadata_sha256": hashlib.sha256(meta_raw).hexdigest(),
               "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
               "subject_sha256": hashlib.sha256(meta["subject"].encode()).hexdigest()}
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=compression) as archive:
        for name, value in (("report.json", raw), ("metadata.json", meta_raw),
                            ("report.txt", body), ("report.html", html),
                            ("delivery-receipt.json", json.dumps(receipt).encode())):
            archive.writestr(name, value)
    return out.getvalue()


def run_record(run_id, generated="2026-11-02T07:32:06+00:00", **changes):
    record = dict(id=run_id, head_branch="main", event="schedule", status="completed", conclusion="success",
                  created_at=generated, run_started_at=generated, run_attempt=1, head_sha="a" * 40,
                  path=".github/workflows/equity-momentum-boundary-weekly.yml",
                  html_url=f"https://github.com/example/repo/actions/runs/{run_id}")
    record.update(changes)
    return record


class RestoreFixTests(unittest.TestCase):
    def invoke(self, runs, payloads, fail_artifact=None, fail_listing=None, pages=None, artifacts=None, attempts=None, artifact_pages=None):
        calls = []
        records = {run["id"]: run for run in (sum(pages.values(), []) if pages else runs if isinstance(runs, list) else [])
                   if isinstance(run, dict) and type(run.get("id")) is int}
        def api(endpoint, binary=False):
            calls.append(endpoint)
            if "/workflows/" in endpoint:
                if isinstance(runs, BaseException):
                    raise runs
                page = int(endpoint.split("&page=")[-1])
                return {"workflow_runs": pages.get(page, []) if pages else runs}
            if "/attempts/" in endpoint:
                run_id = int(endpoint.split("/runs/")[1].split("/")[0])
                attempt_number = int(endpoint.split("/attempts/")[1])
                return attempts[(run_id, attempt_number)] if attempts else records[run_id]
            if "/runs/" in endpoint:
                run_id = int(endpoint.split("/runs/")[1].split("/")[0])
                if fail_listing and run_id == 2:
                    raise fail_listing
                if artifact_pages:
                    page = int(endpoint.split("&page=")[-1])
                    return artifact_pages[(run_id, page)]
                default_name = f"equity-momentum-boundary-{run_id}"
                if records[run_id].get("run_attempt", 1) > 1:
                    default_name += f"-{records[run_id]['run_attempt']}"
                return {"artifacts": artifacts[run_id] if artifacts else [{"id": run_id, "name": default_name, "expired": False}]}
            artifact_id = int(endpoint.split("/artifacts/")[1].split("/")[0])
            if fail_artifact and artifact_id == 2:
                raise fail_artifact
            return payloads[artifact_id]
        with tempfile.TemporaryDirectory() as folder, patch.object(recovery, "api", side_effect=api):
            result = recovery.restore("example/repo", Path(folder), NOW, METHOD_VERSION)
            restored = (Path(folder) / "report.json").exists()
            provenance = json.loads((Path(folder) / "restore.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance, result)
        return result, restored, calls

    def test_bound_delivered_report_is_restored(self):
        result, restored, calls = self.invoke([run_record(1)], {1: bound_archive()})
        self.assertTrue(restored)
        self.assertEqual(result["binding"], "sha256")
        self.assertIn("status=completed", calls[0])
        self.assertNotIn("status=success", calls[0])

    def test_legacy_unbound_archive_is_explicitly_excluded(self):
        result, restored, _ = self.invoke([run_record(1)], {1: bound_archive(bound=False)})
        self.assertFalse(restored)
        self.assertEqual(result["status"], "missing")
        self.assertIn("旧报告缺少数据绑定", result["reason"])

    def test_report_data_changes_break_binding(self):
        original = bound_archive()
        with zipfile.ZipFile(io.BytesIO(original)) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        report = json.loads(files["report.json"])
        report["supplemental"]["sp500_price_momentum"]["momentum_1m"] = .8
        files["report.json"] = json.dumps(report).encode()
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as archive:
            for name, value in files.items():
                archive.writestr(name, value)
        with self.assertRaises(ValueError):
            recovery.verified_report(out.getvalue(), NOW, METHOD_VERSION)

    def test_candidate_http_errors_timeouts_and_connection_errors_fall_back(self):
        errors = [subprocess.CalledProcessError(1, ["gh"], stderr=f"gh: unavailable (HTTP {code})".encode())
                  for code in (404, 410, 500, 503)]
        errors += [subprocess.TimeoutExpired(["gh"], 60), ConnectionResetError("connection reset")]
        for error in errors:
            with self.subTest(error=type(error).__name__, stderr=getattr(error, "stderr", None)):
                result, restored, calls = self.invoke([run_record(2), run_record(1)], {1: bound_archive()}, fail_artifact=error)
                self.assertTrue(restored)
                self.assertEqual(result["run_id"], 1)
                self.assertEqual(len(result["attempts"]), 1)
                self.assertTrue(any("/artifacts/1/zip" in endpoint for endpoint in calls))

    def test_run_artifact_listing_error_is_also_candidate_local(self):
        error = subprocess.CalledProcessError(1, ["gh"], stderr=b"HTTP 404")
        result, restored, _ = self.invoke([run_record(2), run_record(1)], {1: bound_archive()}, fail_listing=error)
        self.assertTrue(restored)
        self.assertEqual(result["run_id"], 1)

    def test_global_auth_and_rate_limits_stop_once_without_scanning_older_candidates(self):
        for code in (401, 403, 429):
            with self.subTest(code=code):
                error = subprocess.CalledProcessError(1, ["gh"], stderr=f"secret-signed-URL (HTTP {code})".encode())
                result, restored, calls = self.invoke([run_record(2), run_record(1)], {}, fail_artifact=error)
                self.assertFalse(restored)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["http_status"], code)
                self.assertFalse(any("/artifacts/1/zip" in endpoint for endpoint in calls))
                self.assertNotIn("secret-signed-URL", json.dumps(result))

    def test_global_run_listing_failure_is_bounded(self):
        error = subprocess.CalledProcessError(1, ["gh"], stderr=b"HTTP 401")
        result, restored, calls = self.invoke(error, {})
        self.assertFalse(restored)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(len(calls), 1)

    def test_bad_deflate_is_skipped_and_healthy_candidate_survives(self):
        payload = bytearray(bound_archive(compression=zipfile.ZIP_DEFLATED))
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            offset = archive.getinfo("report.json").header_offset
        name_len, extra_len = struct.unpack_from("<HH", payload, offset + 26)
        payload[offset + 30 + name_len + extra_len] = 0xFF
        result, restored, _ = self.invoke([run_record(2), run_record(1)], {2: bytes(payload), 1: bound_archive()})
        self.assertTrue(restored)
        self.assertEqual(result["run_id"], 1)
        self.assertEqual(result["attempts"][0]["error_type"], "error")

    def test_unsupported_and_encrypted_zip_are_candidate_local(self):
        for mode in ("unsupported", "encrypted"):
            payload = bytearray(bound_archive())
            central = payload.index(b"PK\x01\x02")
            if mode == "unsupported":
                struct.pack_into("<H", payload, 8, 99)
                struct.pack_into("<H", payload, central + 10, 99)
            else:
                struct.pack_into("<H", payload, 6, 1)
                struct.pack_into("<H", payload, central + 8, 1)
            result, restored, _ = self.invoke([run_record(2), run_record(1)], {2: bytes(payload), 1: bound_archive()})
            self.assertTrue(restored, mode)
            self.assertEqual(result["run_id"], 1)

    def test_late_rerun_uses_actual_attempt_start_not_original_creation(self):
        record = run_record(1, created_at="2026-11-02T07:32:06+00:00",
                            run_started_at="2026-11-06T07:32:06+00:00", run_attempt=2)
        result, restored, _ = self.invoke([record], {1: bound_archive("2026-11-06T07:32:25+00:00")})
        self.assertTrue(restored)
        self.assertEqual(result["run_attempt"], 2)
        self.assertEqual(result["run_started_at"], "2026-11-06T07:32:06+00:00")

    def test_old_run_rerun_is_selected_ahead_of_newer_created_run(self):
        newer_created = run_record(2, "2026-11-05T07:32:06+00:00")
        late_rerun = run_record(1, created_at="2026-11-02T07:32:06+00:00",
                               run_started_at="2026-11-06T07:32:06+00:00", run_attempt=2)
        result, restored, calls = self.invoke([newer_created, late_rerun], {
            1: bound_archive("2026-11-06T07:32:25+00:00"), 2: bound_archive("2026-11-05T07:32:25+00:00")})
        self.assertTrue(restored)
        self.assertEqual(result["run_id"], 1)
        self.assertFalse(any("/artifacts/2/zip" in endpoint for endpoint in calls))

    def test_late_rerun_on_second_api_page_is_not_lost(self):
        first_page = [run_record(i, "2026-11-07T07:32:06+00:00") for i in range(100, 200)]
        rerun = run_record(1, created_at="2026-10-15T07:32:06+00:00",
                           run_started_at="2026-11-08T07:32:06+00:00", run_attempt=3)
        result, restored, _ = self.invoke([], {1: bound_archive("2026-11-08T07:32:25+00:00")},
                                         pages={1: first_page, 2: [rerun]})
        self.assertTrue(restored)
        self.assertEqual(result["run_id"], 1)
        self.assertEqual(result["run_pages_scanned"], 2)

    def test_rerun_without_actual_start_cannot_reuse_original_creation(self):
        record = run_record(1, run_started_at=None, run_attempt=2)
        result, restored, _ = self.invoke([record], {})
        self.assertFalse(restored)
        self.assertEqual(result["status"], "missing")

    def test_failed_run_with_proven_delivery_is_not_excluded_by_conclusion(self):
        result, restored, _ = self.invoke([run_record(1, conclusion="failure")], {1: bound_archive()})
        self.assertTrue(restored)
        self.assertEqual(result["run_id"], 1)

    def test_search_cap_is_explicit_not_an_exhaustive_absence_claim(self):
        future = [run_record(i, "2026-11-10T07:32:06+00:00") for i in range(100)]
        result, restored, calls = self.invoke([], {}, pages={1: future, 2: future, 3: future})
        self.assertFalse(restored)
        self.assertTrue(result["run_search_capped"])
        self.assertIn("检索上限", result["reason"])
        self.assertEqual(len(calls), 3)

    def test_previous_attempt_archive_uses_that_attempt_not_same_week_latest_start(self):
        latest = run_record(1, created_at="2026-11-02T07:32:06+00:00",
                            run_started_at="2026-11-09T08:40:00+00:00", run_attempt=2)
        first = run_record(1, "2026-11-02T07:32:06+00:00")
        artifacts = {1: [{"id": 12, "name": "equity-momentum-boundary-1-2", "expired": False},
                         {"id": 11, "name": "equity-momentum-boundary-1-1", "expired": False}]}
        result, restored, _ = self.invoke([latest], {
            12: bound_archive("2026-11-09T08:41:00+00:00"), 11: bound_archive()}, artifacts=artifacts,
            attempts={(1, 1): first, (1, 2): latest})
        self.assertTrue(restored)
        self.assertEqual(result["artifact_id"], 11)
        self.assertEqual(result["run_attempt"], 1)
        self.assertEqual(result["run_started_at"], "2026-11-02T07:32:06+00:00")

    def test_artifact_workflow_identity_mismatch_is_rejected(self):
        for bad_identity in ({"id": 9, "head_branch": "main", "head_sha": "a" * 40},
                             {"id": 2, "head_branch": "other", "head_sha": "a" * 40},
                             {"id": 2, "head_branch": "main", "head_sha": "b" * 40}):
            artifact_map = {2: [{"id": 2, "name": "equity-momentum-boundary-2", "expired": False,
                                 "workflow_run": bad_identity}],
                            1: [{"id": 1, "name": "equity-momentum-boundary-1", "expired": False}]}
            result, restored, _ = self.invoke([run_record(2), run_record(1)], {1: bound_archive()}, artifacts=artifact_map)
            self.assertTrue(restored)
            self.assertEqual(result["run_id"], 1)

    def test_attempt_endpoint_identity_mismatch_is_rejected(self):
        rerun = run_record(2, run_attempt=2)
        for change in ({"id": 9}, {"head_branch": "other"}, {"head_sha": "b" * 40}, {"run_attempt": 1}):
            bad = dict(rerun, **change)
            result, restored, _ = self.invoke([rerun, run_record(1)], {1: bound_archive()}, attempts={(2, 2): bad})
            self.assertTrue(restored)
            self.assertEqual(result["run_id"], 1)

    def test_malformed_artifact_listing_and_metadata_are_candidate_local(self):
        for malformed in (None, [None]):
            artifact_map = {2: malformed,
                            1: [{"id": 1, "name": "equity-momentum-boundary-1", "expired": False}]}
            result, restored, _ = self.invoke([run_record(2), run_record(1)], {1: bound_archive()}, artifacts=artifact_map)
            self.assertTrue(restored)
            self.assertEqual(result["run_id"], 1)

    def test_candidate_json_decode_failure_does_not_hide_healthy_older_archive(self):
        error = json.JSONDecodeError("synthetic non-JSON response", "", 0)
        result, restored, _ = self.invoke([run_record(2), run_record(1)], {1: bound_archive()}, fail_listing=error)
        self.assertTrue(restored)
        self.assertEqual(result["run_id"], 1)
        self.assertEqual(result["attempts"][0]["error_type"], "JSONDecodeError")

    def test_missing_run_and_artifact_ids_are_isolated(self):
        incomplete_run = run_record(2)
        del incomplete_run["id"]
        result, restored, _ = self.invoke([incomplete_run, run_record(1)], {1: bound_archive()})
        self.assertTrue(restored)
        self.assertEqual(result["run_id"], 1)
        artifact_map = {2: [{"name": "equity-momentum-boundary-2", "expired": False}],
                        1: [{"id": 1, "name": "equity-momentum-boundary-1", "expired": False}]}
        result, restored, _ = self.invoke([run_record(2), run_record(1)], {1: bound_archive()}, artifacts=artifact_map)
        self.assertTrue(restored)
        self.assertEqual(result["run_id"], 1)

    def test_artifact_second_page_can_supply_the_bound_report(self):
        others = [{"id": 1000 + i, "name": f"other-{i}", "expired": False} for i in range(100)]
        last = [{"id": 1, "name": "equity-momentum-boundary-1", "expired": False}]
        result, restored, _ = self.invoke([run_record(1)], {1: bound_archive()},
            artifact_pages={(1, 1): {"total_count": 101, "artifacts": others},
                            (1, 2): {"total_count": 101, "artifacts": last}})
        self.assertTrue(restored)
        self.assertEqual(result["artifact_search"]["1"]["pages_scanned"], 2)
        self.assertFalse(result["artifact_search_capped"])

    def test_artifact_search_cap_is_explicit(self):
        others = [{"id": 1000 + i, "name": f"other-{i}", "expired": False} for i in range(100)]
        result, restored, calls = self.invoke([run_record(1)], {}, artifact_pages={
            (1, page): {"total_count": 400, "artifacts": others} for page in (1, 2, 3)})
        self.assertFalse(restored)
        self.assertTrue(result["artifact_search_capped"])
        self.assertIn("归档分页", result["reason"])
        self.assertIn("不能确认", result["reason"])
        self.assertEqual(len(calls), 4)


if __name__ == "__main__":
    unittest.main()
