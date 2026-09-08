import hashlib
import unittest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from etf_preflight import audit


class AdversarialPreflightTests(unittest.TestCase):
    def setUp(self):
        self.run = {"run_id": "123", "head_sha": "abc", "build_started_at": "2026-09-07T21:00:00Z", "conclusion": "success", "send_gmail": "success", "configured_sources": ["blog"]}
        self.meta = {"subject": "美股 ETF 与资产配置日报 - 2026-09-08", "body": "", "attachment": None}
        self.history = {"head_sha": "abc", "phase": "before_build", "items": []}
        self.manifest = {"run_id": "123", "head_sha": "abc", "cutoff_utc": "2026-09-08T05:00:00+08:00", "capture_mode": "build_snapshot", "configured_sources": ["blog"], "source_audit": [{"source_id": "blog", "coverage": "complete", "evidence": "dated complete archive covers cutoff"}], "candidates": [], "selected_items": [], "history_added_items": []}
        self.item = {"url": "https://example.com/article", "title": "A concentrated world", "source": "blog", "published": "2026-09-07T03:00:00Z", "independent_eligible": True}

    def check(self):
        self.manifest["body_sha256"] = hashlib.sha256(self.meta["body"].encode()).hexdigest()
        return audit(self.manifest, self.meta, self.history, self.run)

    def test_zero_can_pass_only_with_coverage_proof(self):
        self.assertEqual(self.check()["status"], "PASS")
        self.manifest["source_audit"][0]["evidence"] = ""
        self.assertEqual(self.check()["status"], "PARTIAL")

    def test_same_day_prior_success_is_still_deduped(self):
        self.manifest["candidates"] = [self.item]
        self.history["items"] = [{**self.item, "sent_date": "2026-09-08"}]
        result = self.check()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["decisions"][0]["decision"], "previously_sent")

    def test_eligible_unseen_item_is_confirmed_omission(self):
        self.manifest["candidates"] = [self.item]
        self.assertEqual(self.check()["failures"][0]["reason"], "confirmed_omission")

    def test_missing_feed_is_partial_even_when_zero_candidates(self):
        self.manifest["configured_sources"].append("podcast")
        self.assertEqual(self.check()["status"], "PARTIAL")

    def test_current_rss_replay_never_proves_historical_pass(self):
        self.manifest["capture_mode"] = "live_refetch"
        self.assertEqual(self.check()["status"], "PARTIAL")

    def test_exact_cutoff_timezone_and_post_cutoff(self):
        self.manifest["candidates"] = [{**self.item, "published": "2026-09-08T05:00:01+08:00"}]
        self.assertEqual(self.check()["decisions"][0]["decision"], "after_cutoff")
        self.manifest["cutoff_utc"] = "2026-09-08T05:00:00"
        self.assertEqual(self.check()["status"], "PARTIAL")

    def test_36_hour_boundary_inclusive(self):
        self.manifest["candidates"] = [{**self.item, "published": "2026-09-06T09:00:00Z"}]
        self.assertEqual(self.check()["status"], "FAILED")
        self.manifest["candidates"][0]["published"] = "2026-09-06T08:59:59Z"
        self.assertEqual(self.check()["status"], "PASS")

    def test_unrendered_selection_and_sent_history_are_failed(self):
        self.manifest["selected_items"] = [self.item]
        self.manifest["history_added_items"] = [self.item]
        reasons = {item["reason"] for item in self.check()["failures"]}
        self.assertEqual(reasons, {"selected_but_not_rendered", "unrendered_item_written_to_sent_history"})

    def test_audit_url_does_not_count_as_email_selection(self):
        self.meta["body"] = "| Source | https://example.com/article | 未确认 |"
        self.manifest["candidates"] = [self.item]
        self.assertEqual(self.check()["status"], "FAILED")

    def test_documented_capacity_is_not_scraper_omission(self):
        self.manifest["candidates"] = [{**self.item, "exclusion_reason": "editorial_capacity", "exclusion_evidence": "rank 10; 9 higher-ranked independently verified entries rendered"}]
        self.assertEqual(self.check()["status"], "PASS")

    def test_same_broken_filter_cannot_certify_itself(self):
        self.manifest["candidates"] = [{**self.item, "independent_eligible": None, "exclusion_reason": "score_below_55"}]
        self.assertEqual(self.check()["status"], "PARTIAL")

    def test_aggregator_requires_child_review(self):
        self.meta["body"] = "- 链接：https://example.com/article"
        self.manifest["selected_items"] = [self.item]
        self.manifest["candidates"] = [{**self.item, "is_aggregator": True}]
        self.assertEqual(self.check()["status"], "PARTIAL")

    def test_failed_has_priority_over_partial(self):
        self.manifest["source_audit"] = []
        self.manifest["candidates"] = [self.item]
        self.assertEqual(self.check()["status"], "FAILED")

    def test_history_must_be_prebuild_same_head(self):
        self.history["phase"] = "after_send"
        self.assertEqual(self.check()["status"], "PARTIAL")

    def test_legacy_without_snapshot_is_partial(self):
        self.assertEqual(audit({}, self.meta, self.history, self.run)["gaps"][0]["reason"], "legacy_email_missing_collection_snapshot")

    def test_body_hash_must_bind_to_actual_sent_body(self):
        self.manifest["body_sha256"] = "incorrect"
        self.assertEqual(audit(self.manifest, self.meta, self.history, self.run)["status"], "PARTIAL")

    def test_selected_post_cutoff_item_is_rejected(self):
        self.meta["body"] = "- 链接：https://example.com/article"
        self.manifest["selected_items"] = [self.item]
        self.manifest["candidates"] = [{**self.item, "published": "2026-09-08T05:01:00+08:00"}]
        self.assertEqual(self.check()["failures"][0]["reason"], "after_cutoff_item_rendered")

    def test_duplicate_rendered_is_rejected(self):
        self.meta["body"] = "- 链接：https://example.com/article"
        self.manifest["selected_items"] = [self.item]
        self.manifest["candidates"] = [self.item]
        self.history["items"] = [{**self.item, "sent_date": "2026-09-08"}]
        self.assertEqual(self.check()["failures"][0]["reason"], "duplicate_item_rendered")

    def test_unconfirmed_send_cannot_pass(self):
        self.run["send_gmail"] = "skipped"
        self.assertEqual(self.check()["status"], "PARTIAL")

    def test_report_date_uses_beijing_not_utc(self):
        self.meta["subject"] = "美股 ETF 与资产配置日报 - 2026-09-07"
        self.assertEqual(self.check()["status"], "PARTIAL")

    def test_manifest_cannot_hide_a_configured_source(self):
        self.run["configured_sources"].append("unavailable_podcast")
        self.assertEqual(self.check()["status"], "PARTIAL")

    def test_run_identity_is_required_even_when_both_sides_omit_it(self):
        del self.run["run_id"]
        del self.manifest["run_id"]
        self.assertEqual(self.check()["status"], "PARTIAL")

    def test_script_start_inside_actual_build_step_is_cutoff(self):
        self.run["build_started_at"] = "2026-09-07T20:59:58Z"
        self.run["build_completed_at"] = "2026-09-07T21:02:00Z"
        self.manifest["candidates"] = [{**self.item, "published": "2026-09-07T20:59:59Z"}]
        result = self.check()
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["failures"][0]["reason"], "confirmed_omission")
        self.assertEqual(result["cutoff_utc"], "2026-09-07T21:00:00+00:00")
        self.assertFalse(any("cutoff" in gap["reason"] for gap in result["gaps"]))

    def test_snapshot_outside_build_step_cannot_pass(self):
        self.run["build_started_at"] = "2026-09-07T21:00:01Z"
        self.run["build_completed_at"] = "2026-09-07T21:02:00Z"
        self.assertEqual(self.check()["status"], "PARTIAL")
        self.run["build_started_at"] = "2026-09-07T20:58:00Z"
        self.run["build_completed_at"] = "2026-09-07T20:59:59Z"
        self.assertEqual(self.check()["status"], "PARTIAL")

    def test_legacy_proof_without_completion_keeps_exact_comparison(self):
        self.run["build_started_at"] = "2026-09-07T20:59:59Z"
        self.assertEqual(self.check()["status"], "PARTIAL")

    def test_naive_build_completion_cannot_pass(self):
        self.run["build_completed_at"] = "2026-09-07T21:02:00"
        self.assertEqual(self.check()["status"], "PARTIAL")

    def test_invalid_cutoff_cannot_manufacture_confirmed_omission(self):
        self.run["build_started_at"] = "2026-09-07T20:58:00Z"
        self.run["build_completed_at"] = "2026-09-07T20:59:59Z"
        self.manifest["candidates"] = [self.item]
        result = self.check()
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["failures"], [])


if __name__ == "__main__":
    unittest.main()
