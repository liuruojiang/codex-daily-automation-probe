"""Independent counterexamples for the send-time omission audit.

The public abstract is frozen from arXiv v1, not a live network dependency.
Synthetic dates below exercise the audit window, not historical availability.
No test calls delivery code or uses private historical email artifacts.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from etf_candidate_audit import audit_candidates


CAPACITY_ABSTRACT = (
    "How much capital a trading strategy can absorb before its edge disappears is a causal question about how much is deployed, but it is answered with observational proxies that rest on incompatible assumptions. "
    "We ask what experiment would answer it instead, and show that two features of the problem interact to constrain any answer. "
    "Deployed capital erodes the edge gradually, so a trial of fixed length measures less than the eventual effect; and parallel implementations of one strategy trade the same securities, so they are not independent units. "
    "Comparing implementations on the same date removes market-wide shocks, which is what makes the comparison credible. "
    "But the crowding created by the strategy's own accumulated position is common to those implementations too, and an arbitrary date effect absorbs it exactly: the comparison that makes the experiment robust is the one that prevents it from measuring the crowding capacity is about. "
    "A same-date design recovers one implementation's private response at the prevailing level of aggregate positioning, and reaching the aggregate effect requires either implementations with deliberately different exposure to that position or variation in it over time. "
    "We characterise what each route identifies and what it costs, establish how far a fixed holding period understates the eventual effect and how to correct for it, and show what a finite set of deployment levels can and cannot reveal. "
    "A calibration on a purpose-built panel illustrates the resulting design rules and prices a study that would follow them."
)


class OmissionAdversarialTests(unittest.TestCase):
    def setUp(self):
        self.item = {
            "source": "arXiv q-fin.PM",
            "source_id": "research|arXiv q-fin.PM|https://rss.arxiv.org/rss/q-fin.PM",
            "title": "Robustness or Crowding: Experimental Design for Trading Strategy Capacity",
            "url": "https://arxiv.org/abs/2608.08405",
            "published": "2026-09-07T12:00:00Z",
            "summary": CAPACITY_ABSTRACT,
            "enrichment": {"summary": CAPACITY_ABSTRACT, "attempted": True, "body_paragraphs": 1},
            "independent_eligible": False,
            "pipeline_decision": {"reason": "evidence_below_threshold", "score": 0},
        }
        self.history = {"head_sha": "test-sha", "phase": "before_build", "items": []}
        self.manifest = {
            "schema_version": 2,
            "decision_policy": "etf-candidates-v1",
            "head_sha": "test-sha",
            "run_id": "test-run",
            "capture_mode": "build_snapshot",
            "cutoff_utc": "2026-09-07T21:00:00Z",
            "configured_sources": [self.item["source_id"]],
            "source_audit": [{"source_id": self.item["source_id"], "coverage": "complete", "status": "read", "evidence": {"captured_count": 1}}],
            "candidates": [self.item],
            "selected_items": [],
            "history_added_items": [],
        }

    def check(self):
        return audit_candidates(deepcopy(self.manifest), deepcopy(self.history))

    def select(self):
        self.manifest["selected_items"] = [deepcopy(self.item)]

    def reasons(self, result, kind="failures"):
        return {row["reason"] for row in result[kind]}

    def test_real_methodology_paper_omission_blocks_despite_pipeline_false(self):
        result = self.check()
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("unexplained_priority_omission", self.reasons(result))

    def test_pipeline_score_and_editorial_label_cannot_self_certify_exclusion(self):
        for reason in ("score_below_55", "unrelated", "evidence_below_threshold", "editorial_capacity"):
            with self.subTest(reason=reason):
                self.item["pipeline_decision"] = {"reason": reason, "score": 0, "evidence": "editor decided"}
                self.item["exclusion_reason"] = reason
                self.item["exclusion_evidence"] = "9 higher ranked articles exist"
                self.assertEqual(self.check()["status"], "FAILED")

    def test_full_methodology_paper_selection_resolves_omission(self):
        self.select()
        result = self.check()
        self.assertNotEqual(result["status"], "FAILED", result)
        self.assertNotIn("unexplained_priority_omission", self.reasons(result))

    def test_descriptive_body_can_establish_topic_despite_generic_blog_title(self):
        self.item["title"] = "Fourteen questions I am thinking about"
        self.item["summary"] = CAPACITY_ABSTRACT
        self.assertIn("unexplained_priority_omission", self.reasons(self.check()))

    def test_future_candidate_not_a_missing_item_but_cannot_be_selected(self):
        self.item["published"] = "2026-09-07T21:00:01Z"
        self.assertNotEqual(self.check()["status"], "FAILED")
        self.select()
        self.assertEqual(self.check()["status"], "FAILED")

    def test_item_exactly_at_cutoff_is_not_treated_as_future(self):
        self.item["published"] = self.manifest["cutoff_utc"]
        self.assertIn("unexplained_priority_omission", self.reasons(self.check()))

    def test_naive_and_missing_dates_are_unconfirmed_never_passed(self):
        for published in ("2026-09-07T12:00:00", "", "not-a-date"):
            with self.subTest(published=published):
                self.item["published"] = published
                self.assertNotEqual(self.check()["status"], "PASS")
                self.select()
                self.assertEqual(self.check()["status"], "FAILED")
                self.manifest["selected_items"] = []

    def test_old_item_not_omitted_but_cannot_be_selected_as_new(self):
        self.item["published"] = "2026-01-01T00:00:00Z"
        self.assertNotEqual(self.check()["status"], "FAILED")
        self.select()
        self.assertEqual(self.check()["status"], "FAILED")

    def test_same_day_sent_url_tracking_variant_is_not_a_new_omission(self):
        self.history["items"] = [{"url": self.item["url"] + "?utm_source=email", "title": self.item["title"], "sent_date": "2026-09-08"}]
        self.assertNotEqual(self.check()["status"], "FAILED")
        self.select()
        self.assertEqual(self.check()["status"], "FAILED")

    def test_prior_sent_title_under_different_url_is_duplicate(self):
        self.history["items"] = [{"url": "https://example.test/syndicated", "title": self.item["title"], "sent_date": "2026-09-07"}]
        self.assertNotEqual(self.check()["status"], "FAILED")
        self.select()
        self.assertEqual(self.check()["status"], "FAILED")

    def test_malformed_history_date_cannot_excuse_a_current_omission(self):
        self.history["items"] = [{"url": self.item["url"], "title": self.item["title"], "sent_date": "2026-09-07garbage"}]
        result = self.check()
        self.assertNotEqual(result["status"], "PASS", result)
        self.assertFalse(any(row.get("decision") == "previously_sent" for row in result["decisions"]))

    def test_empty_and_script_only_body_never_selected_as_evidence(self):
        for summary in ("", "<script>" + CAPACITY_ABSTRACT + "</script>", "<nav>" + CAPACITY_ABSTRACT + "</nav>"):
            with self.subTest(summary=summary[:20]):
                self.item["summary"] = summary
                self.item["enrichment"] = {"summary": summary, "attempted": True, "body_paragraphs": 9}
                self.select()
                self.assertEqual(self.check()["status"], "FAILED")

    def test_pure_advertisement_cannot_be_selected_by_finance_keywords(self):
        self.item["title"] = "Sponsored promotion: subscribe to our ETF portfolio trading service"
        text = ("Subscribe now and start your free trial. Sign up today for exclusive investment portfolio deals. "
                "Buy now to claim this limited time offer. Use coupon code ETF for a discount on our trading subscription. ") * 3
        self.item["summary"] = text
        self.item["enrichment"] = {"summary": text, "attempted": True, "body_paragraphs": 9}
        self.select()
        self.assertEqual(self.check()["status"], "FAILED")

    def test_missing_body_has_explicit_review_queue_not_silent_exclusion(self):
        self.item["summary"] = ""
        self.item["enrichment"] = {"summary": "", "attempted": True, "body_paragraphs": 0}
        result = self.check()
        self.assertNotEqual(result["status"], "PASS")
        recorded = [*result["decisions"], *result["gaps"], *result["failures"]]
        self.assertTrue(any(row.get("url") == self.item["url"] for row in recorded), result)

    def test_audit_does_not_mutate_manifest_or_history(self):
        before_manifest, before_history = deepcopy(self.manifest), deepcopy(self.history)
        audit_candidates(self.manifest, self.history)
        self.assertEqual(self.manifest, before_manifest)
        self.assertEqual(self.history, before_history)

    def test_each_input_candidate_receives_a_decision(self):
        others = []
        for n, date in enumerate(("2026-01-01T00:00:00Z", "2026-09-09T00:00:00Z", "")):
            row = deepcopy(self.item)
            row.update(url=f"https://example.test/candidate-{n}", title=f"Portfolio risk fixture {n}", published=date)
            others.append(row)
        self.manifest["candidates"].extend(others)
        result = self.check()
        represented = {row.get("url") for row in [*result["decisions"], *result["gaps"], *result["failures"]]}
        self.assertTrue({row["url"] for row in self.manifest["candidates"]} <= represented)

    def test_bogus_stored_pass_cannot_override_recomputed_omission(self):
        self.manifest["candidate_audit"] = {"status": "PASS", "failures": [], "gaps": [], "decisions": []}
        self.manifest["candidate_ledger"] = [{"url": self.item["url"], "decision": "excluded", "reason": "low_score"}]
        self.assertEqual(self.check()["status"], "FAILED")

    def test_removing_unselected_candidate_breaks_source_capture_count(self):
        self.manifest["candidates"] = []
        self.assertEqual(self.check()["status"], "FAILED")

    def test_selected_item_cannot_appear_without_capture(self):
        self.select()
        self.manifest["candidates"] = []
        self.manifest["source_audit"][0]["evidence"]["captured_count"] = 0
        self.assertEqual(self.check()["status"], "FAILED")

    def test_same_run_title_syndication_dedupe_is_not_a_priority_omission(self):
        self.select()
        duplicate = deepcopy(self.item)
        duplicate["url"] = "https://example.test/syndicated-capacity-paper"
        duplicate["pipeline_decision"] = {"reason": "same_run_duplicate"}
        self.manifest["candidates"].append(duplicate)
        self.manifest["source_audit"][0]["evidence"]["captured_count"] = 2
        result = self.check()
        self.assertNotEqual(result["status"], "FAILED", result)
        self.assertNotIn("unexplained_priority_omission", self.reasons(result))

    def test_two_unselected_same_title_candidates_cannot_excuse_each_other(self):
        duplicate = deepcopy(self.item)
        duplicate["url"] = "https://example.test/syndicated-capacity-paper"
        self.manifest["candidates"].append(duplicate)
        self.manifest["source_audit"][0]["evidence"]["captured_count"] = 2
        self.assertIn("unexplained_priority_omission", self.reasons(self.check()))

    def test_research_fourteen_day_backfill_boundary_remains_allowed(self):
        self.item["published"] = "2026-08-24T21:00:00Z"
        self.select()
        self.assertNotEqual(self.check()["status"], "FAILED")
        self.item["published"] = "2026-08-24T20:59:59Z"
        self.select()
        self.assertEqual(self.check()["status"], "FAILED")

    def test_forum_fourteen_day_window_is_not_accidentally_cut_to_36_hours(self):
        for kind in ("forum", "reddit"):
            with self.subTest(kind=kind):
                sid = f"{kind}|fixture|https://example.test/forum"
                self.item["source_id"] = sid
                self.item["published"] = "2026-08-25T12:00:00Z"
                self.item["pipeline_decision"] = {"reason": "rendered"}
                self.manifest["configured_sources"] = [sid]
                self.manifest["source_audit"][0]["source_id"] = sid
                self.select()
                self.assertNotEqual(self.check()["status"], "FAILED")

    def add_fixed_capture(self):
        duplicate = deepcopy(self.item)
        duplicate["source_id"] = self.item["source_id"].replace("research|", "fixed_feed|", 1)
        duplicate["summary"] = "Short feed teaser."
        duplicate.pop("enrichment", None)
        self.manifest["candidates"].append(duplicate)
        self.manifest["configured_sources"].append(duplicate["source_id"])
        self.manifest["source_audit"].append({"source_id": duplicate["source_id"], "coverage": "complete", "evidence": {"captured_count": 1}})
        return duplicate

    def test_research_backfill_also_captured_by_fixed_feed_is_one_article(self):
        self.item["published"] = "2026-09-05T12:00:00Z"
        self.select()
        duplicate = self.add_fixed_capture()
        # This order also occurs with concurrent collection; attribution must
        # not depend on encountering the research row first.
        self.manifest["candidates"].reverse()
        result = self.check()
        self.assertEqual(result["status"], "PASS", result)
        self.assertTrue(any(row["decision"] == "same_run_research_capture_duplicate" for row in result["decisions"]))
        self.item["summary"] = "Insufficient evidence."
        self.item.pop("enrichment")
        self.assertIn("selected_evidence_requires_review", self.reasons(self.check()))

    def test_duplicate_capture_cannot_extend_research_backfill_window(self):
        self.item["published"] = "2026-08-24T20:59:59Z"
        self.select()
        self.add_fixed_capture()
        self.assertIn("stale_item_rendered", self.reasons(self.check()))

    def test_fixed_only_backfill_still_fails(self):
        self.item["published"] = "2026-09-05T12:00:00Z"
        duplicate = self.add_fixed_capture()
        self.manifest["candidates"] = [duplicate]
        self.manifest["source_audit"][0]["evidence"]["captured_count"] = 0
        self.select()
        self.assertIn("stale_item_rendered", self.reasons(self.check()))

    def test_conflicting_capture_identity_is_not_excused_by_shared_url(self):
        self.item["published"] = "2026-09-05T12:00:00Z"
        self.select()
        duplicate = self.add_fixed_capture()
        original = deepcopy(duplicate)
        for field, value in (("published", "2026-09-04T12:00:00Z"), ("title", "Another portfolio article"), ("source", "Other publisher")):
            with self.subTest(field=field):
                duplicate.update(original)
                duplicate[field] = value
                self.assertIn("stale_item_rendered", self.reasons(self.check()))

    def test_local_preflight_unverified_cutoff_does_not_certify_timed_omission(self):
        from etf_preflight import audit
        metadata = {"body": "# Audit fixture", "attachment": None,
                    "subject": "美股 ETF 与资产配置日报 - 2026-09-08"}
        self.manifest["body_sha256"] = hashlib.sha256(metadata["body"].encode()).hexdigest()
        run = {"run_id": "test-run", "head_sha": "test-sha", "conclusion": "success", "send_gmail": "success",
               "configured_sources": self.manifest["configured_sources"],
               "build_started_at": "2026-09-07T20:58:00Z", "build_completed_at": "2026-09-07T20:59:59Z"}
        result = audit(self.manifest, metadata, self.history, run)
        self.assertNotIn("unexplained_priority_omission", self.reasons(result))
        self.assertEqual(result["status"], "PARTIAL")


if __name__ == "__main__":
    unittest.main()
