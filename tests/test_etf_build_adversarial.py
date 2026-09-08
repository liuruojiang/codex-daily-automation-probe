from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import daily_reports as dr
from etf_preflight import rendered_urls
from validate_etf_delivery import validate


class EtfBuildAdversarialTests(unittest.TestCase):
    def setUp(self):
        self.cutoff = datetime(2026, 9, 7, 5, 0, tzinfo=dr.BJ)  # Monday source-only path
        self.items = [dr.Item("Independent fixture", f"Portfolio risk study {i}", f"https://example.com/study-{i}", "2026-09-06T12:00:00Z", f"Distinct study {i} investigates portfolio risk constraints and rebalancing.") for i in range(6)]
        self.scored = [dr.ScoredResearchItem(item, 90, "学术研究", "风险研究", (dr.QUANT_STRATEGY_SECTION,), ("fixture",)) for item in self.items]

    def test_more_than_four_in_one_section_all_render(self):
        lines = []
        dr.append_etf_research_sections(lines, self.scored, [], include_market_summary=False)
        self.assertEqual(rendered_urls("\n".join(lines)), {item.url for item in self.items})

    def test_future_filter_uses_frozen_start_not_later_wall_clock(self):
        now_item = dr.Item("x", "at start", "https://example.com/at", self.cutoff.isoformat(), "")
        future_item = dr.Item("x", "after start", "https://example.com/after", (self.cutoff + timedelta(seconds=1)).isoformat(), "")
        token = dr.ETF_BUILD_CUTOFF.set(self.cutoff)
        try:
            with patch.object(dr, "now_bj", return_value=self.cutoff + timedelta(hours=5)):
                self.assertEqual(dr.filter_recent_published([now_item, future_item], 36), [now_item])
        finally:
            dr.ETF_BUILD_CUTOFF.reset(token)

    def test_build_emits_bound_artifacts_and_excludes_unrendered_forum_history(self):
        hidden = dr.Item("Bogleheads", "Insufficient forum evidence", "https://example.com/hidden-forum", "2026-09-06T12:00:00Z", "short")
        previous = {"sent_date": "2026-09-07", "source": "old", "title": "Earlier same-day successful email", "url": "https://example.com/earlier-same-day"}
        feed = dr.ResearchFeed("Independent fixture", "https://example.com/feed", "学术研究", "风险研究", (dr.QUANT_STRATEGY_SECTION,), 10)
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            original_cwd = Path.cwd()
            os.chdir(temp)
            stack.callback(os.chdir, original_cwd)
            Path("digest_history").mkdir()
            Path("digest_history/etf.json").write_text(json.dumps({"items": [previous]}), encoding="utf-8")
            stack.enter_context(patch.dict(os.environ, {"GITHUB_RUN_ID": "fixture-run", "GITHUB_SHA": "fixture-sha"}))
            stack.enter_context(patch.object(dr, "now_bj", return_value=self.cutoff))
            stack.enter_context(patch.object(dr, "ETF_RESEARCH_FEEDS", (feed,)))
            for name in ("ETF_FIXED_MONITOR_FEEDS", "ETF_FIXED_PAGE_MONITORS", "ETF_FORUM_SUBREDDITS", "ETF_EXTERNAL_FORUM_FEEDS"):
                stack.enter_context(patch.object(dr, name, ()))
            stack.enter_context(patch.object(dr, "parse_feed", return_value=self.items))
            stack.enter_context(patch.object(dr, "select_etf_research_items", return_value=self.scored))
            stack.enter_context(patch.object(dr, "collect_etf_forum_items", return_value=[hidden]))
            stack.enter_context(patch.object(dr, "select_etf_forum_items", return_value=[hidden]))
            stack.enter_context(patch.object(dr, "ensure_non_reddit_forum_mix", return_value=[hidden]))
            stack.enter_context(patch.object(dr, "forum_thread_summary_points", return_value=[]))
            stack.enter_context(patch.object(dr, "forum_has_specific_summary_evidence", return_value=False))
            stack.enter_context(patch.object(dr, "forum_lightweight_summary_points", return_value=[]))
            stack.enter_context(patch.object(dr, "forum_has_lightweight_summary_evidence", return_value=False))
            out = Path(temp) / "artifacts"
            dr.build_etf(out)
            metadata = json.loads((out / "metadata.json").read_text(encoding="utf-8"))
            manifest = json.loads((out / "collection_manifest.json").read_text(encoding="utf-8"))
            before = json.loads((out / "history_before.json").read_text(encoding="utf-8"))
            after = json.loads(Path("digest_history/etf.json").read_text(encoding="utf-8"))
            self.assertEqual(validate(metadata, manifest), [])
            self.assertEqual(before["items"], [previous])
            self.assertEqual(before["phase"], "before_build")
            self.assertEqual(before["head_sha"], "fixture-sha")
            self.assertEqual(len(manifest["selected_items"]), 6)
            self.assertEqual(len(manifest["candidates"]), 6)
            self.assertEqual(manifest["source_audit"][0]["evidence"]["captured_count"], 6)
            self.assertEqual(manifest["source_audit"][0]["coverage"], "partial")
            self.assertEqual(manifest["cutoff_utc"], self.cutoff.astimezone(dr.timezone.utc).isoformat())
            self.assertNotIn(hidden.url, {item["url"] for item in after["items"]})
            self.assertIn(previous, after["items"])
            self.assertIsNone(dr.ETF_BUILD_CUTOFF.get())
            self.assertIsNone(dr.ETF_COLLECTION_SNAPSHOT.get())

    def test_failed_build_restores_context(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(dr, "_build_etf", side_effect=RuntimeError("injected failure")):
                with self.assertRaisesRegex(RuntimeError, "injected failure"):
                    dr.build_etf(Path(temp))
        self.assertIsNone(dr.ETF_BUILD_CUTOFF.get())
        self.assertIsNone(dr.ETF_COLLECTION_SNAPSHOT.get())


if __name__ == "__main__":
    unittest.main()
