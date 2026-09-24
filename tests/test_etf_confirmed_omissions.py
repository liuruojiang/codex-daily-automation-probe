import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import daily_reports as dr
from etf_candidate_audit import audit_candidates
from etf_preflight import rendered_urls
from validate_etf_delivery import validate


class ConfirmedOmissionTests(unittest.TestCase):
    def test_next_weekend_email_includes_carryover_and_sent_history(self) -> None:
        row = {
            "origin_run_id": "old-run", "origin_report_date": "2026-09-22",
            "source": "Publisher", "title": "ETF research missed in prior email",
            "zh_title": "前期遗漏的 ETF 研究", "url": "https://example.com/missed-etf",
            "published": "2026-09-21T20:00:00+00:00",
            "summary": (
                "The publisher explains that this ETF follows an industry classification that excludes several prominent companies. "
                "It also describes how adding the fund to a broad portfolio changes concentration rather than necessarily increasing diversification."
            ),
            "zh_summary": "原文核对了行业分类和组合持仓重合。",
            "evidence_level": "publisher_full_article",
            "verify_next": "复核基金持仓。",
        }
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            old_cwd = Path.cwd()
            os.chdir(temp)
            stack.callback(os.chdir, old_cwd)
            Path("digest_history").mkdir()
            Path("digest_history/etf.json").write_text('{"items": []}', encoding="utf-8")
            Path("digest_history/etf_confirmed_omissions.json").write_text(
                json.dumps({"schema_version": 1, "items": [row]}), encoding="utf-8")
            stack.enter_context(patch.dict(os.environ, {"GITHUB_RUN_ID": "new-run", "GITHUB_SHA": "new-sha"}))
            stack.enter_context(patch.object(dr, "now_bj", return_value=datetime(2026, 9, 28, 5, tzinfo=dr.BJ)))
            for name in ("ETF_RESEARCH_FEEDS", "ETF_FIXED_MONITOR_FEEDS", "ETF_FIXED_PAGE_MONITORS",
                         "ETF_FORUM_SUBREDDITS", "ETF_EXTERNAL_FORUM_FEEDS"):
                stack.enter_context(patch.object(dr, name, ()))
            stack.enter_context(patch.object(dr, "collect_etf_forum_items", return_value=[]))
            out = Path(temp) / "artifacts"
            dr.build_etf(out)
            metadata = json.loads((out / "metadata.json").read_text(encoding="utf-8"))
            manifest = json.loads((out / "collection_manifest.json").read_text(encoding="utf-8"))
            history = json.loads(Path("digest_history/etf.json").read_text(encoding="utf-8"))
            self.assertEqual(validate(metadata, manifest), [])
            self.assertIn("前期确认漏项补送", metadata["body"])
            self.assertIn(row["url"], rendered_urls(metadata["body"]))
            self.assertIn(row["url"], {item["url"] for item in manifest["selected_items"]})
            self.assertIn(row["url"], {item["url"] for item in history["items"]})
            self.assertEqual(json.loads((out / "candidate_audit.json").read_text(encoding="utf-8"))["status"], "PASS")

    def test_queue_renders_once_and_audits_after_primary_window(self) -> None:
        row = {
            "origin_run_id": "old-run", "origin_report_date": "2026-09-22",
            "source": "Publisher", "title": "A missed ETF research article",
            "zh_title": "此前遗漏的 ETF 研究",
            "url": "https://example.com/research/one",
            "published": "2026-09-21T20:00:00+00:00",
            "summary": (
                "The publisher describes how the fund's index definition excludes several large companies that many investors expect to own. "
                "It also explains that combining the fund with a broad market fund can increase concentration in the companies already held by both funds."
            ),
            "zh_summary": "原文讨论指数行业分类和持仓重合。",
            "evidence_level": "publisher_full_article",
            "verify_next": "复核指数规则和当期持仓。",
        }
        cutoff = datetime(2026, 10, 20, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp:
            old_cwd = Path.cwd()
            try:
                os.chdir(temp)
                path = Path("digest_history/etf_confirmed_omissions.json")
                path.parent.mkdir()
                path.write_text(json.dumps({"schema_version": 1, "items": [row]}), encoding="utf-8")
                token = dr.ETF_BUILD_CUTOFF.set(cutoff)
                snapshot_token = dr.ETF_COLLECTION_SNAPSHOT.set({"source_audit": [], "candidates": []})
                try:
                    entries = dr.load_etf_confirmed_omissions({"items": []})
                    self.assertEqual(len(entries), 1)
                    lines: list[str] = []
                    dr.append_etf_confirmed_omissions(lines, entries)
                    self.assertEqual(rendered_urls("\n".join(lines)), {row["url"]})
                    snapshot = dr.ETF_COLLECTION_SNAPSHOT.get()
                finally:
                    dr.ETF_COLLECTION_SNAPSHOT.reset(snapshot_token)
                    dr.ETF_BUILD_CUTOFF.reset(token)
            finally:
                os.chdir(old_cwd)
        manifest = {
            "schema_version": 2, "decision_policy": "etf-candidates-v1",
            "cutoff_utc": cutoff.isoformat(), "head_sha": "fixture",
            "configured_sources": [snapshot["source_audit"][0]["source_id"]],
            "source_audit": snapshot["source_audit"], "candidates": snapshot["candidates"],
            "selected_items": [{"title": row["title"], "url": row["url"]}],
        }
        manifest["candidates"][0]["pipeline_decision"] = {"reason": "rendered", "evidence_gate": True}
        history = {"phase": "before_build", "head_sha": "fixture", "items": []}
        result = audit_candidates(manifest, history)
        self.assertEqual(result["status"], "PASS", result)
        # The original feed may still list the same URL with an empty excerpt.
        original_capture = dict(snapshot["candidates"][0])
        original_capture.update({"source_id": "research|Publisher|https://example.com/feed",
                                 "summary": "", "confirmed_omission": False,
                                 "pipeline_decision": {"reason": "body_insufficient"}})
        manifest["configured_sources"].append(original_capture["source_id"])
        manifest["source_audit"].append({"source_id": original_capture["source_id"],
                                         "coverage": "partial", "status": "bounded_listing_read",
                                         "evidence": {"captured_count": 1}})
        manifest["candidates"].append(original_capture)
        result = audit_candidates(manifest, history)
        self.assertEqual(result["failures"], [], result)

    def test_sent_history_suppresses_queue_item(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            old_cwd = Path.cwd()
            try:
                os.chdir(temp)
                path = Path("digest_history/etf_confirmed_omissions.json")
                path.parent.mkdir()
                path.write_text(json.dumps({"schema_version": 1, "items": [{
                    "origin_run_id": "old-run", "origin_report_date": "2026-09-22",
                    "source": "Publisher", "title": "Already sent", "zh_title": "已发",
                    "url": "https://example.com/already-sent",
                    "published": "2026-09-21T20:00:00+00:00",
                    "summary": "A complete original summary with enough detail for a fact-based reader to understand the item.",
                    "zh_summary": "已发内容。", "evidence_level": "publisher_full_article",
                    "verify_next": "无。",
                }]}), encoding="utf-8")
                token = dr.ETF_BUILD_CUTOFF.set(datetime(2026, 9, 25, tzinfo=timezone.utc))
                try:
                    result = dr.load_etf_confirmed_omissions({"items": [{
                        "sent_date": "2026-09-24", "title": "Already sent",
                        "url": "https://example.com/already-sent",
                    }]})
                finally:
                    dr.ETF_BUILD_CUTOFF.reset(token)
                self.assertEqual(result, [])
            finally:
                os.chdir(old_cwd)


if __name__ == "__main__":
    unittest.main()
