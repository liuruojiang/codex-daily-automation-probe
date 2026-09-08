from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import daily_reports as dr


class EvidenceAdversarialTests(unittest.TestCase):
    prose = (
        "We study diversification of portfolio risks under uncertainty about dependence between assets. "
        "The model compares robust allocation objectives and shows that the selected portfolio depends on the uncertainty assumptions. "
        "These theoretical risk results do not establish net outperformance after transaction costs."
    )

    def item(self, title="New allocation research", summary=None):
        return dr.Item("arXiv q-fin.PM", title, "https://arxiv.org/abs/example", "2026-09-07T00:00:00Z", self.prose if summary is None else summary)

    def test_known_title_without_source_text_is_rejected(self):
        for title in ("World Markets Watchlist", "Tactical Yield", "Volatility Forecasts Lead to Better Portfolios"):
            with self.subTest(title=title):
                item = self.item(title, "")
                self.assertFalse(dr.etf_has_enough_summary_evidence(item))
                self.assertNotIn("24.0%", dr.etf_chinese_fact(item))

    def test_unseen_research_passes_without_title_template(self):
        self.assertTrue(dr.etf_has_enough_summary_evidence(self.item()))
        self.assertIn("已取得可读原文", dr.etf_chinese_fact(self.item()))

    def test_keyword_negation_does_not_generate_absent_number(self):
        sentence = "We do not find that equities should replace cash, and we do not study commodities."
        self.assertNotIn("6.8%", dr.detail_sentence_chinese_summary(sentence))
        self.assertEqual(dr.etf_article_detail_points(self.item("Commodity Futures Returns Since 1871")), [])

    def test_script_html_is_not_article_evidence(self):
        page = '<script>const x = "<p>' + self.prose + '</p>";</script><p>Short text.</p>'
        self.assertEqual(dr.article_evidence_paragraphs(page), [])
        self.assertEqual(dr.fixed_page_article_summary(page), "")
        self.assertFalse(dr.etf_has_enough_summary_evidence(self.item(summary=page)))

    def test_article_scope_excludes_nav_and_related_cards(self):
        page = '<nav><p>Fake navigation claims. ' + self.prose + '</p></nav><article><p>' + self.prose + '</p></article><aside><p>UNRELATED RELATED CARD ' + self.prose + '</p></aside>'
        self.assertEqual(dr.article_evidence_paragraphs(page), [self.prose])

    def test_arxiv_abstract_blockquote_is_readable(self):
        self.assertEqual(dr.article_evidence_paragraphs('<blockquote class="abstract">' + self.prose + '</blockquote>'), [self.prose])

    def test_full_arxiv_feed_abstract_retains_late_limitations(self):
        abstract = self.prose * 4 + " End-of-abstract limitation: dependence assumptions remain unverified."
        xml = '<rss><channel><item><title>New research</title><link>https://arxiv.org/abs/example</link><pubDate>Mon, 07 Sep 2026 00:00:00 GMT</pubDate><description>' + abstract + '</description></item></channel></rss>'
        with patch.object(dr, "fetch_bytes", return_value=xml.encode()):
            item = dr.parse_feed("arXiv q-fin.PM", "https://example.test/feed")[0]
        self.assertIn("End-of-abstract limitation", item.summary)

    def test_enrichment_failure_preserves_full_official_abstract(self):
        item = self.item(summary=self.prose * 5)
        with patch.object(dr, "fetch_bytes", side_effect=TimeoutError):
            enriched = dr.enrich_article_item(item)
        self.assertEqual(enriched.summary, item.summary)

    def test_related_card_date_does_not_date_undated_article(self):
        page = '<article><p>Undated original paper.</p></article><aside><time datetime="2026-09-08T00:00:00Z">Related item</time></aside>'
        self.assertIsNone(dr.fixed_page_publication_date(page))
        self.assertIsNone(dr.fixed_page_publication_date('<script>{"datePublished":"2026-09-08"}</script>'))

    def test_document_meta_and_article_jsonld_dates_work(self):
        pages = [
            '<head><meta content="2026-09-07T00:00:00Z" property="article:published_time"></head>',
            '<script type="application/ld+json">{"@type":"BlogPosting","datePublished":"2026-09-07T00:00:00Z"}</script>',
            '<article><time datetime="2026-09-07T00:00:00Z">Published</time></article>',
        ]
        for page in pages:
            self.assertEqual(dr.fixed_page_publication_date(page).isoformat(), "2026-09-07T00:00:00+00:00")

    def test_conflicting_article_dates_are_unconfirmed(self):
        page = '<script type="application/ld+json">[{"@type":"Article","datePublished":"2026-09-07"},{"@type":"Article","datePublished":"2026-09-08"}]</script>'
        self.assertIsNone(dr.fixed_page_publication_date(page))

    def test_yoast_webpage_date_requires_canonical_identity(self):
        page = '<head><link rel="canonical" href="https://example.org/current"></head><script type="application/ld+json">{"@type":"WebPage","url":"https://example.org/current","datePublished":"2026-09-07"}</script>'
        self.assertEqual(dr.fixed_page_publication_date(page).date().isoformat(), "2026-09-07")
        self.assertIsNone(dr.fixed_page_publication_date(page.replace('"url":"https://example.org/current"', '"url":"https://example.org/related"')))
        related_article = page.replace('"WebPage"', '"BlogPosting"').replace('"url":"https://example.org/current"', '"url":"https://example.org/related"')
        self.assertIsNone(dr.fixed_page_publication_date(related_article))


if __name__ == "__main__":
    unittest.main()
