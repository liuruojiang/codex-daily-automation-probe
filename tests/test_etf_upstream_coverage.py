from __future__ import annotations

import html
import json
from dataclasses import asdict
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import daily_reports as dr
from etf_candidate_audit import audit_candidates


class UpstreamCoverageTests(unittest.TestCase):
    def setUp(self):
        self.cutoff = dr.datetime(2026, 10, 22, 0, 4, 15, tzinfo=dr.timezone.utc)
        self.token = dr.ETF_BUILD_CUTOFF.set(self.cutoff)
        self.rows = json.loads((ROOT / "tests/fixtures/etf_upstream_20261022/omissions.json").read_text("utf-8"))

    def tearDown(self):
        dr.ETF_BUILD_CUTOFF.reset(self.token)

    def rss(self, rows, summary=""):
        # Reconstructed XML from frozen public metadata, not an archived feed.
        items = [f"<item><title>{html.escape(r['title'])}</title><link>{html.escape(r['url'])}</link>"
                 f"<pubDate>{r['published']}</pubDate><description>{html.escape(summary)}</description></item>"
                 for r in rows]
        return ("<rss><channel>" + "".join(items) + "</channel></rss>").encode()

    def test_real_omission_metadata_beyond_fifth_is_captured_and_reviewable(self):
        rows = [r for r in self.rows if r["source"] == "Seeking Alpha ETFs"]
        self.assertEqual(len(rows), 19)
        feed = next(f for f in dr.ETF_RESEARCH_FEEDS if f.source == "Seeking Alpha ETFs")
        snapshot = {"source_audit": [], "candidates": []}
        token = dr.ETF_COLLECTION_SNAPSHOT.set(snapshot)
        try:
            with patch.object(dr, "ETF_RESEARCH_FEEDS", (feed,)), patch.object(dr, "fetch_bytes", return_value=self.rss(rows)):
                items, audit = dr.collect_etf_research_sources()
            self.assertEqual({x.url for x in items}, {r["url"] for r in rows})
            self.assertEqual(len(snapshot["candidates"]), 19)
            self.assertEqual(audit[0][2], 19)
            self.assertEqual(snapshot["source_audit"][0]["coverage"], "partial")
            # Metadata-only feeds cannot manufacture article evidence.
            self.assertTrue(all(x.summary == "" for x in items))
            with patch.object(dr, "article_paragraphs", return_value=[]):
                enriched = dr.enrich_ranked_research_items(items, limit=30)
            self.assertEqual(enriched, [])
            self.assertTrue(all(x.get("enrichment", {}).get("attempted") for x in snapshot["candidates"]))
            sid = snapshot["source_audit"][0]["source_id"]
            snapshot.update(schema_version=2, decision_policy="etf-candidates-v1",
                            cutoff_utc=self.cutoff.isoformat(), head_sha="fixture-head",
                            configured_sources=[sid], selected_items=[])
            audit = dr.finalize_etf_candidate_decisions(snapshot, [],
                        {"phase": "before_build", "head_sha": "fixture-head", "items": []})
            review_urls = {dr.canonical_url(x["url"]) for x in audit["gaps"] if x.get("url")}
            self.assertTrue({dr.canonical_url(r["url"]) for r in rows} <= review_urls)
        finally:
            dr.ETF_COLLECTION_SNAPSHOT.reset(token)

    def test_fixed_monitor_feed_is_not_cut_to_presentation_limit(self):
        rows = [r for r in self.rows if r["source"] == "Seeking Alpha ETFs"]
        feed = dr.FixedMonitorFeed("Fixed source replay", "https://example.com/feed", "博客", 5)
        with patch.object(dr, "ETF_FIXED_MONITOR_FEEDS", (feed,)), \
             patch.object(dr, "ETF_FIXED_PAGE_MONITORS", ()), \
             patch.object(dr, "fetch_bytes", return_value=self.rss(rows)), \
             patch.object(dr, "load_digest_history", return_value={"items": []}), \
             patch.object(dr, "fixed_monitor_item_with_evidence", side_effect=lambda x: x):
            items, audit = dr.collect_etf_fixed_monitor_updates_with_audit()
        self.assertEqual(len(items), 19)
        self.assertEqual(audit[0][2], 19)

    def test_low_score_does_not_prevent_original_source_retrieval(self):
        rows = [r for r in self.rows if r["source"] == "Seeking Alpha ETFs"]
        items = [dr.Item(r["source"], r["title"], r["url"], r["published"], "") for r in rows]
        paragraphs = ["The fund holds a diversified basket of securities and its financing costs are compared against the broad market benchmark.",
                      "The author reports that higher interest rates can affect portfolio income while concentration remains an additional source of risk."]
        with patch.object(dr, "article_paragraphs", return_value=paragraphs) as read:
            picked = dr.enrich_ranked_research_items(items, limit=30)
        self.assertEqual({x.item.url for x in picked}, {x.url for x in items})
        self.assertEqual(read.call_count, 19)

    def test_research_risk_discussion_is_not_product_suitability_screen(self):
        item = dr.Item("Seeking Alpha ETFs", "PSQ: A Sell Into Big Tech Earnings And The Fed",
                       "https://seekingalpha.com/article/4955858-psq", "2026-10-21T12:34:43Z",
                       "The inverse ETF can lose money while leveraged market exposure changes its risk profile.")
        self.assertIsNotNone(dr.score_etf_research_item(item))
        marketing = dr.Item(item.source, "YieldMax single-stock weeklypay launch", item.url, item.published, item.summary)
        self.assertIsNone(dr.score_etf_research_item(marketing))

    def research_audit(self, title, summary, shown=False):
        item = dr.Item("Seeking Alpha ETFs", title, "https://example.com/risk-study",
                       "2026-10-21T12:00:00Z", summary)
        sid = "research|Seeking Alpha ETFs|https://example.com/feed"
        row = asdict(item) | {"source_id": sid, "pipeline_decision": {"reason": "editorial_capacity"}}
        manifest = {
            "schema_version": 2, "decision_policy": "etf-candidates-v1",
            "cutoff_utc": self.cutoff.isoformat(), "head_sha": "fixture",
            "configured_sources": [sid],
            "source_audit": [{"source_id": sid, "coverage": "complete", "evidence": {"captured_count": 1}}],
            "candidates": [row], "selected_items": [asdict(item)] if shown else [],
        }
        history = {"phase": "before_build", "head_sha": "fixture", "items": []}
        return item, audit_candidates(manifest, history)

    def test_exposure_risk_research_omissions_cannot_pass_independent_audit(self):
        body = (
            "The portfolio model compares financing costs across different market environments and measures how investors can experience losses during prolonged volatile conditions. "
            "Researchers evaluate daily compounding against a diversified benchmark because concentrated exposure creates additional uncertainty in the distribution of future outcomes."
        )
        for exposure in ("Leveraged ETF", "Inverse ETF", "ETF"):
            with self.subTest(exposure=exposure):
                title = exposure + " portfolio risks and financing costs"
                item, audit = self.research_audit(title, body)
                self.assertTrue(dr.etf_research_relevant(item))
                self.assertIsNotNone(dr.score_etf_research_item(item))
                self.assertTrue(dr.etf_has_enough_summary_evidence(item))
                self.assertEqual(audit["status"], "FAILED")
                self.assertIn("unexplained_priority_omission", {r["reason"] for r in audit["failures"]})
                self.assertEqual(self.research_audit(title, body, shown=True)[1]["status"], "PASS")

    def test_exposure_titles_do_not_supply_missing_body_evidence(self):
        for title in ("Leveraged ETF portfolio risks", "Inverse ETF portfolio risks"):
            with self.subTest(title=title):
                _, audit = self.research_audit(title, "")
                self.assertEqual(audit["status"], "PARTIAL")
                self.assertIn("candidate_needs_independent_review", {r["reason"] for r in audit["gaps"]})
                _, shown_audit = self.research_audit(title, "", shown=True)
                self.assertEqual(shown_audit["status"], "FAILED")
        _, excluded = self.research_audit("YieldMax single-stock weeklypay launch", "")
        self.assertEqual(excluded["decisions"][0]["decision"], "excluded_product_subject")

    def test_freshness_and_sent_history_still_control_selection(self):
        row = self.rows[0]
        items = [dr.Item(row["source"], row["title"], row["url"], row["published"], ""),
                 dr.Item(row["source"], "Future ETF publication", "https://example.com/future", "2026-10-22T01:00:00Z", ""),
                 dr.Item(row["source"], "Undated ETF publication", "https://example.com/undated", "", "")]
        recent = dr.filter_recent_published(items, 36)
        self.assertEqual(recent, items[:1])
        history = {"items": [{"url": row["url"], "title": row["title"], "sent_date": "2026-10-21"}]}
        with patch.object(dr, "load_digest_history", return_value=history):
            self.assertEqual(dr.filter_previously_sent("etf", recent, days=365), [])

    def test_rss_retains_distinct_paragraphs_and_dublin_core_date(self):
        xml = b'''<rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><item>
        <title>ETF portfolio funding risks</title><link>https://example.com/article</link><dc:date>2026-10-21T12:00:00Z</dc:date>
        <description><![CDATA[<p>The fund holds a diversified basket of securities and its financing costs are compared against the broad market benchmark</p>
        <p>The author reports that higher interest rates can affect portfolio income while concentration remains an additional source of risk</p>]]></description>
        </item></channel></rss>'''
        with patch.object(dr, "fetch_bytes", return_value=xml):
            item = dr.parse_feed("Seeking Alpha ETFs", "https://example.com/feed", None)[0]
        self.assertEqual(item.published, "2026-10-21T12:00:00+00:00")
        self.assertTrue(dr.etf_has_enough_summary_evidence(item))

    def test_etfcom_transcript_monitor_checks_article_publication_date(self):
        row = self.rows[-1]
        monitor = next(m for m in dr.ETF_FIXED_PAGE_MONITORS if m.source == "ETF.com Conferences")
        landing = f'<a href="{row["url"]}">{row["title"]}</a>'.encode()
        article = ('<head><meta property="article:published_time" content="2026-10-21T17:11:46Z"></head>'
                   '<article><h1>An Engineer\'s Guide to the AI Trade</h1><p>The speakers compare software business models with investment in computing hardware and describe the resulting capital spending risks.</p></article>').encode()
        with patch.object(dr, "fetch_bytes", side_effect=lambda url, **kw: landing if url == monitor.url else article):
            items = dr.fixed_page_items(monitor)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].url, row["url"])
        self.assertTrue(dr.fixed_monitor_title_relevant(items[0]))
        self.assertEqual(items[0].published, "2026-10-21T17:11:46+00:00")
        # A discovery timestamp or copyright date is never publication evidence.
        with patch.object(dr, "fetch_bytes", side_effect=lambda url, **kw: landing if url == monitor.url else article.replace(b'article:published_time', b'crawl_time')):
            self.assertEqual(dr.fixed_page_items(monitor), [])

    def test_etfcom_external_links_are_rejected_before_fetch(self):
        monitor = next(m for m in dr.ETF_FIXED_PAGE_MONITORS if m.source == "ETF.com Conferences")
        invalid = (
            "https://other.example/sections/conferences/growing-tomatoes",
            "https://www.etf.com.evil.example/sections/conferences/portfolio",
            "https://www.etf.com@evil.example/sections/conferences/portfolio",
            "https://user@www.etf.com/sections/conferences/portfolio",
            "ftp://www.etf.com/sections/conferences/portfolio",
            "https://www.etf.com:8443/sections/conferences/portfolio",
            "https://www.etf.com:bad/sections/conferences/portfolio",
            "https://www.etf.com/sections/conferences/nested/portfolio",
        )
        landing = "".join(f'<a href="{url}">ETF portfolio study</a>' for url in invalid).encode()
        with patch.object(dr, "fetch_bytes", return_value=landing) as fetch:
            self.assertEqual(dr.fixed_page_items(monitor), [])
        self.assertEqual([c.args[0] for c in fetch.call_args_list], [monitor.url])
        for url in invalid:
            with self.subTest(url=url):
                item = dr.Item("ETF.com Conferences", "ETF portfolio study", url, "2026-10-21T12:00:00Z", "")
                self.assertFalse(dr.fixed_monitor_title_relevant(item))

    def test_etfcom_relative_links_and_publisher_alias_remain_supported(self):
        monitor = next(m for m in dr.ETF_FIXED_PAGE_MONITORS if m.source == "ETF.com Conferences")
        article = (b'<meta property="article:published_time" content="2026-10-21T17:11:46Z">'
                   b'<article><h1>An interview with an engineer</h1><p>The speakers compare software business models with investment in computing hardware and describe the resulting capital spending risks.</p></article>')
        for href in ("/sections/conferences/interview", "https://etf.com/sections/conferences/interview",
                     "https://www.etf.com:443/sections/conferences/interview"):
            with self.subTest(href=href):
                landing = f'<a href="{href}">Interview</a>'.encode()
                with patch.object(dr, "fetch_bytes", side_effect=lambda url, **kw: landing if url == monitor.url else article):
                    items = dr.fixed_page_items(monitor)
                self.assertEqual(len(items), 1)
                self.assertTrue(dr.fixed_monitor_title_relevant(items[0]))


if __name__ == "__main__":
    unittest.main()
