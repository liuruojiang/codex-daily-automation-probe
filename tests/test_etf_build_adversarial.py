from __future__ import annotations

import json
import os
import subprocess
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

    def test_bad_individual_etf_does_not_block_research_or_delivery(self):
        cutoff = datetime(2026, 11, 10, 5, 30, tzinfo=dr.BJ)
        session, previous = "2026-11-09", "2026-11-06"
        quote_time = int(datetime(2026, 11, 9, 21, tzinfo=dr.timezone.utc).timestamp())
        feed = dr.ResearchFeed("Independent fixture", "https://example.com/feed", "学术研究", "风险研究", (dr.QUANT_STRATEGY_SECTION,), 10)
        items = [dr.Item(item.source, item.title, item.url, "2026-11-09T12:00:00Z", item.summary) for item in self.items]
        scored = [dr.ScoredResearchItem(item, 90, "学术研究", "风险研究", (dr.QUANT_STRATEGY_SECTION,), ("fixture",)) for item in items]
        mover = {"symbol": "SPY", "name": "Broad market ETF", "description": "fixture",
                 "date": session, "change": 1.0, "category": "us_large_cap",
                 "average_daily_volume": 100000, "average_daily_dollar_volume": 10000000}
        rankings = dr.broad_etf_movers.RankingResult(100, 80, {}, [mover], [], session)
        for failure in ("missing_quote", "stale_quote", "mismatched_close", "missing_previous_close", "none"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
                stack.enter_context(patch.object(dr, "fetch_bytes", side_effect=AssertionError("unexpected network request")))
                original_cwd = Path.cwd()
                os.chdir(temp)
                stack.callback(os.chdir, original_cwd)
                quotes = [{"symbol": symbol, "regularMarketTime": quote_time,
                           "regularMarketPrice": 101.0, "regularMarketChangePercent": 1.0}
                          for symbol in ["SPY", *(asset.symbol for asset in dr.B_STRATEGY_ASSETS)]]
                if failure == "missing_quote":
                    quotes = [row for row in quotes if row["symbol"] != "VGLT"]
                elif failure == "stale_quote":
                    next(row for row in quotes if row["symbol"] == "VGLT")["regularMarketTime"] -= 3 * 86400

                def history(symbol, *args):
                    if symbol == "VGLT" and failure == "missing_previous_close":
                        return [(session, 101.0)]
                    return [(previous, 100.0), (session, 102.0 if symbol == "VGLT" and failure == "mismatched_close" else 101.0)]

                stack.enter_context(patch.object(dr, "now_bj", return_value=cutoff))
                stack.enter_context(patch.object(dr, "ETF_RESEARCH_FEEDS", (feed,)))
                for name in ("A_STRATEGY_ASSETS", "ADK_STRATEGY_ASSETS", "D_STRATEGY_ASSETS"):
                    stack.enter_context(patch.object(dr, name, []))
                for name in ("ETF_FIXED_MONITOR_FEEDS", "ETF_FIXED_PAGE_MONITORS", "ETF_FORUM_SUBREDDITS", "ETF_EXTERNAL_FORUM_FEEDS"):
                    stack.enter_context(patch.object(dr, name, ()))
                stack.enter_context(patch.object(dr, "parse_feed", return_value=items))
                stack.enter_context(patch.object(dr, "select_etf_research_items", return_value=scored))
                stack.enter_context(patch.object(dr, "collect_etf_forum_items", return_value=[]))
                stack.enter_context(patch.object(dr, "yahoo_daily_rows", side_effect=history))
                stack.enter_context(patch.object(dr.broad_etf_movers, "fetch_universe", return_value=quotes))
                stack.enter_context(patch.object(dr.broad_etf_movers, "daily_rankings", return_value=rankings))
                out = Path(temp) / "artifacts"
                dr.build_etf(out)
                metadata = json.loads((out / "metadata.json").read_text(encoding="utf-8"))
                manifest = json.loads((out / "collection_manifest.json").read_text(encoding="utf-8"))
                before = json.loads((out / "history_before.json").read_text(encoding="utf-8"))
                self.assertEqual(validate(metadata, manifest, before, require_candidate_audit=True), [])
                result = subprocess.run([sys.executable, str(ROOT / "scripts/validate_etf_delivery.py"),
                                         str(out / "metadata.json")], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                delivery = json.loads(result.stdout)
                self.assertEqual(delivery["missing_strategy_etfs"], [] if failure == "none" else ["VGLT"])
                if failure != "none":
                    self.assertEqual(delivery["status"], "PASS_WITH_WARNINGS")
                self.assertEqual(rendered_urls(metadata["body"]), {item.url for item in items})
                self.assertIn("| QQQM | 2026-11-09 | +1.00% |", metadata["body"])
                market = manifest["market_data"]
                self.assertEqual(market["us_strategy_expected_count"], 10)
                if failure == "none":
                    self.assertEqual(market["us_strategy_missing_codes"], [])
                    self.assertEqual(len(market["us_strategy_rows"]), 10)
                    self.assertNotIn("策略行情缺项", metadata["body"])
                else:
                    self.assertEqual(market["us_strategy_missing_codes"], ["VGLT"])
                    self.assertEqual(len(market["us_strategy_rows"]), 9)
                    marker = "策略行情缺项（2026-11-09）：VGLT"
                    self.assertIn(marker, metadata["body"])
                    self.assertIn(marker, metadata["html_body"])
                    self.assertNotIn("| VGLT |", metadata["body"])


if __name__ == "__main__":
    unittest.main()
