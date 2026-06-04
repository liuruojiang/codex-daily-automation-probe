from __future__ import annotations

import sys
import unittest
import re
import json
import os
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import daily_reports as dr  # noqa: E402


class EtfDigestQualityTests(unittest.TestCase):
    def item(self, source: str, title: str, summary: str = "", url: str = "https://example.com/x") -> dr.Item:
        return dr.Item(source=source, title=title, url=url, published="2026-05-15T12:00:00+00:00", summary=summary)

    def test_h20955_uses_csindex_total_return_series(self) -> None:
        asset = next(asset for asset in dr.A_STRATEGY_ASSETS if asset.code == "H20955")
        self.assertEqual(asset.source, "csindex")
        self.assertEqual(asset.symbol, "H20955")
        self.assertNotIn("代理", asset.description)

        original = dr.csindex_daily_rows
        try:
            dr.csindex_daily_rows = lambda symbol, lmt=80: [("2026-05-21", 22658.01), ("2026-05-22", 22562.82)]
            date_s, change = dr.asset_change(asset)
            self.assertEqual(date_s, "2026-05-22")
            self.assertAlmostEqual(change, -0.420116329721798)
        finally:
            dr.csindex_daily_rows = original

    def test_etf_dedupe_keeps_recently_sent_items_out_beyond_one_day(self) -> None:
        old_item = self.item(
            "Quantpedia",
            "Dual Momentum Allocation Between Physical Gold and Bitcoin (Digital Gold)",
            "Gold and bitcoin dual momentum allocation.",
            "https://quantpedia.com/dual-momentum-allocation-between-physical-gold-and-bitcoin-digital-gold/",
        )
        new_item = self.item(
            "AQR Insights",
            "Fresh Trend Following Research Note",
            "New trend-following evidence.",
            "https://www.aqr.com/insights/fresh-trend-following-research-note",
        )
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history_dir = tmp_path / "digest_history"
            history_dir.mkdir()
            (history_dir / "etf.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "sent_date": "2026-05-18",
                                "source": old_item.source,
                                "title": old_item.title,
                                "url": old_item.url,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            os.chdir(tmp_path)
            try:
                filtered = dr.filter_previously_sent("etf", [old_item, new_item], days=dr.ETF_DEDUPE_DAYS)
            finally:
                os.chdir(original_cwd)

        self.assertEqual([item.url for item in filtered], [new_item.url])

    def test_mover_universe_excludes_a_share_local_indexes(self) -> None:
        codes = {asset.code for asset in dr.MOVER_UNIVERSE}
        self.assertNotIn("000852", codes)
        self.assertNotIn("000300", codes)
        self.assertNotIn("000905", codes)
        self.assertNotIn("399006", codes)
        self.assertIn("ASHR", codes)
        self.assertIn("FXI", codes)

    def test_etf_research_freshness_filter_drops_stale_rss_items(self) -> None:
        original_now_bj = dr.now_bj
        dr.now_bj = lambda: dr.datetime(2026, 5, 20, 7, 0, tzinfo=dr.BJ)
        try:
            fresh = dr.Item("Quantpedia", "Fresh item", "https://example.com/fresh", "2026-05-19T16:00:00+00:00", "")
            stale = dr.Item("Quantpedia", "Stale item", "https://example.com/stale", "2026-05-18T00:00:00+00:00", "")
            filtered = dr.filter_recent_published([fresh, stale], dr.ETF_ARTICLE_MAX_AGE_HOURS)
        finally:
            dr.now_bj = original_now_bj

        self.assertEqual([item.title for item in filtered], ["Fresh item"])

    def test_etf_research_selector_backfills_high_evidence_items_when_primary_is_empty(self) -> None:
        original_now_bj = dr.now_bj
        dr.now_bj = lambda: dr.datetime(2026, 5, 21, 7, 0, tzinfo=dr.BJ)
        fresh_low_evidence = dr.Item(
            "ETF Trends",
            "Fresh ETF market color",
            "https://example.com/fresh-color",
            "2026-05-20T18:00:00+00:00",
            "Subscribe for more market updates. Related ETFs moved today.",
        )
        older_high_evidence = dr.Item(
            "Quantpedia",
            "Commodity Futures Returns Since 1871",
            "https://quantpedia.com/commodity-futures-returns-since-1871/",
            "2026-05-16T12:00:00+00:00",
            (
                "The article reports an average annual risk premium for commodity futures "
                "relative to the risk-free rate of 5.4%, a real return premium above 6%, "
                "and compares it with equities earning about 6.8% over cash."
            ),
        )
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                picked = dr.select_etf_research_items([fresh_low_evidence, older_high_evidence], limit=3)
            finally:
                os.chdir(original_cwd)
                dr.now_bj = original_now_bj

        self.assertEqual([x.item.url for x in picked], [older_high_evidence.url])

    def test_arxiv_volatility_forecast_abstract_counts_as_specific_evidence(self) -> None:
        item = dr.Item(
            "arXiv q-fin.PM",
            "Do Better Volatility Forecasts Lead to Better Portfolios? Evidence from Graph Neural Networks",
            "https://arxiv.org/abs/2605.19278",
            "2026-05-20T12:00:00+00:00",
            (
                "This paper tests whether graph neural networks improve realized volatility forecasts "
                "and whether those forecasts improve portfolio performance. Using weekly realized "
                "volatility for 465 S&P 500 equities from 2015-2025, HAR and LSTM baselines are "
                "compared against GraphSAGE models built on rolling correlation, sector similarity, "
                "and supply-chain network features."
            ),
        )

        self.assertTrue(dr.etf_has_enough_summary_evidence(item))

    def test_etf_forum_selector_excludes_recently_sent_renderable_threads(self) -> None:
        original_now_bj = dr.now_bj
        dr.now_bj = lambda: dr.datetime(2026, 5, 21, 7, 0, tzinfo=dr.BJ)
        renderable = dr.Item(
            "Reddit r/ETFs",
            "SCHG vs QQQM for long term?",
            "https://www.reddit.com/r/ETFs/comments/example/schg_vs_qqqm/",
            "2026-05-19T12:00:00+00:00",
            (
                "The post compares portfolio allocation choices between SCHG and QQQM. "
                "Replies discuss whether the overlap creates concentrated growth exposure, "
                "whether the choice belongs in a long-term core portfolio, and how this affects rebalance decisions."
            ),
        )
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history_dir = tmp_path / "digest_history"
            history_dir.mkdir()
            (history_dir / "etf.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "sent_date": "2026-05-19",
                                "source": renderable.source,
                                "title": renderable.title,
                                "url": renderable.url,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            os.chdir(tmp_path)
            try:
                picked = dr.select_etf_forum_items([renderable], limit=3)
            finally:
                os.chdir(original_cwd)
                dr.now_bj = original_now_bj

        self.assertEqual(picked, [])

    def test_bogleblog_bestof_index_yields_bogleheads_forum_items(self) -> None:
        original_fetch = dr.fetch_bytes

        def fake_fetch(url: str, timeout: int = 30, headers: dict[str, str] | None = None) -> bytes:
            self.assertEqual(url, dr.BOGLEBLOG_BEST_OF_BOGLEHEADS_URL)
            return b"""
            <html><body>
              <a href="https://www.bogleheads.org/forum/viewtopic.php?t=407430">Transition to 3 Fund Portfolio</a>
              <a href="https://www.bogleheads.org/forum/viewtopic.php?t=287967">Overall index of portfolios</a>
              <a href="https://example.com/not-forum">Ignore me</a>
            </body></html>
            """

        try:
            dr.fetch_bytes = fake_fetch
            items = dr.bogleblog_bestof_forum_items(limit=5)
        finally:
            dr.fetch_bytes = original_fetch

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].source, "Bogleblog Best of Bogleheads Forum")
        self.assertIn("Transition to 3 Fund Portfolio", dr.forum_display_title(items[0]))
        self.assertIn("三基金组合", dr.forum_display_title(items[0]))
        self.assertTrue(all(dr.etf_forum_relevant(item) for item in items))

    def test_collect_etf_forum_items_includes_live_non_reddit_forums_without_static_index(self) -> None:
        original_parse_feed = dr.parse_feed
        original_fetch_reddit = dr.fetch_reddit_listing_items
        original_bogleblog = dr.bogleblog_bestof_forum_items
        try:
            dr.fetch_reddit_listing_items = lambda subreddit, sort="hot", limit=12: []
            dr.parse_feed = lambda source, url, limit=12: [
                self.item(
                    source,
                    "Bogleheads living in South Korea - How are we doing?",
                    "Forum thread discusses Bogleheads portfolio allocation and long-term ETF core holdings.",
                    url,
                )
            ] if source == "Bogleheads.org Forum" else []
            dr.bogleblog_bestof_forum_items = lambda limit=8: [
                self.item(
                    "Bogleblog Best of Bogleheads Forum",
                    "Transition to 3 Fund Portfolio",
                    "Curated Bogleheads forum index item about portfolio allocation, ETF core holdings, and rebalancing.",
                    "https://www.bogleheads.org/forum/viewtopic.php?t=407430",
                )
            ]

            items = dr.collect_etf_forum_items()
        finally:
            dr.parse_feed = original_parse_feed
            dr.fetch_reddit_listing_items = original_fetch_reddit
            dr.bogleblog_bestof_forum_items = original_bogleblog

        sources = {item.source for item in items}
        self.assertIn("Bogleheads.org Forum", sources)
        self.assertNotIn("Bogleblog Best of Bogleheads Forum", sources)

    def test_collect_etf_forum_items_always_queries_supplemental_reddit_sorts(self) -> None:
        original_parse_feed = dr.parse_feed
        original_fetch_reddit = dr.fetch_reddit_listing_items
        calls: list[tuple[str, str, int]] = []

        def fake_fetch_reddit(subreddit: str, sort: str = "hot", limit: int = 12) -> list[dr.Item]:
            calls.append((subreddit, sort, limit))
            return [
                self.item(
                    f"Reddit r/{subreddit}",
                    f"Portfolio allocation discussion {sort} {idx}",
                    "Thread discusses ETF core holdings, risk tolerance, bond allocation, and rebalance decisions.",
                    f"https://www.reddit.com/r/{subreddit}/comments/example/{sort}_{idx}/",
                )
                for idx in range(8)
            ]

        try:
            dr.fetch_reddit_listing_items = fake_fetch_reddit
            dr.parse_feed = lambda source, url, limit=12: []

            dr.collect_etf_forum_items()
        finally:
            dr.parse_feed = original_parse_feed
            dr.fetch_reddit_listing_items = original_fetch_reddit

        called_sorts = {(subreddit, sort) for subreddit, sort, _limit in calls}
        self.assertIn(("ETFs", "hot"), called_sorts)
        self.assertIn(("ETFs", "top"), called_sorts)
        self.assertIn(("ETFs", "new"), called_sorts)
        self.assertGreaterEqual(min(limit for subreddit, _sort, limit in calls if subreddit == "ETFs"), 24)

    def test_etf_forum_selector_recovers_when_unrendered_same_day_candidates_polluted_history(self) -> None:
        original_now_bj = dr.now_bj
        dr.now_bj = lambda: dr.datetime(2026, 5, 21, 7, 0, tzinfo=dr.BJ)
        renderable = dr.Item(
            "Reddit r/ETFs",
            "SCHG vs QQQM for long term?",
            "https://www.reddit.com/r/ETFs/comments/example/schg_vs_qqqm/",
            "2026-05-21T01:00:00+00:00",
            (
                "The post compares portfolio allocation choices between SCHG and QQQM. "
                "Replies discuss whether the overlap creates concentrated growth exposure, "
                "whether the choice belongs in a long-term core portfolio, and how this affects rebalance decisions."
            ),
        )
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history_dir = tmp_path / "digest_history"
            history_dir.mkdir()
            (history_dir / "etf.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "sent_date": "2026-05-21",
                                "source": renderable.source,
                                "title": renderable.title,
                                "url": renderable.url,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            os.chdir(tmp_path)
            try:
                picked = dr.select_etf_forum_items([renderable], limit=3)
            finally:
                os.chdir(original_cwd)
                dr.now_bj = original_now_bj

        self.assertEqual([x.url for x in picked], [renderable.url])

    def test_relevance_score_prefers_research_over_noisy_product_news(self) -> None:
        quant = self.item(
            "Quantpedia",
            "Momentum Strategies After Transaction Costs",
            "Backtested factor rotation with capacity, turnover, and no-trade region constraints.",
            "https://quantpedia.com/momentum-costs",
        )
        noisy = self.item(
            "ETF Trends",
            "Single-Stock ETF Surges After One Company Beats Earnings",
            "Product news focused on a single-stock daily target ETF.",
            "https://www.etftrends.com/single-stock-surge",
        )

        ranked = dr.rank_etf_research_items([noisy, quant], limit=5)

        self.assertEqual([x.item.source for x in ranked], ["Quantpedia"])
        self.assertGreaterEqual(ranked[0].score, 70)
        self.assertIn("量化策略影响", ranked[0].sections)
        self.assertNotIn("A 股 / 港股专项", ranked[0].sections)

    def test_section_mapping_separates_allocation_quant_and_china(self) -> None:
        blackrock = self.item(
            "BlackRock Investment Institute",
            "Capital market assumptions update",
            "Long-term expected returns, correlations, risk budgeting, and strategic asset allocation.",
            "https://www.blackrock.com/us/financial-professionals/insights/capital-market-assumptions",
        )
        arxiv = self.item(
            "arXiv q-fin.PM",
            "Robust Portfolio Optimization with Turnover Constraints",
            "A reproducible portfolio construction paper with backtests and transaction cost controls.",
            "https://arxiv.org/abs/2605.00001",
        )
        hkex = self.item(
            "HKEX Market Communications",
            "Stock Connect trading calendar and market communication",
            "Hong Kong market structure, ETF trading, and Stock Connect rule update.",
            "https://www.hkex.com.hk/Services/RSS-Feeds/market-communications",
        )

        scored = dr.rank_etf_research_items([blackrock, arxiv, hkex], limit=10)
        by_source = {x.item.source: x for x in scored}

        self.assertIn("资产配置影响", by_source["BlackRock Investment Institute"].sections)
        self.assertIn("量化策略影响", by_source["arXiv q-fin.PM"].sections)
        self.assertIn("A 股 / 港股专项", by_source["HKEX Market Communications"].sections)

        hypotheses = dr.build_etf_testable_hypotheses(scored, limit=3)
        self.assertLessEqual(len(hypotheses), 3)
        self.assertTrue(all("验证" in x or "回测" in x or "检验" in x for x in hypotheses))

    def test_article_section_renderer_uses_new_research_framework(self) -> None:
        items = dr.rank_etf_research_items(
            [
                self.item(
                    "AQR Insights",
                    "Trend Following and Diversification",
                    "Factor investing, trend following, market risk, and portfolio construction.",
                    "https://www.aqr.com/Insights",
                ),
                self.item(
                    "Robot Wealth",
                    "No-trade region for ETF portfolios",
                    "Trading costs, rebalance bands, turnover, and practical implementation.",
                    "https://robotwealth.com/no-trade-region",
                ),
            ],
            limit=10,
        )
        forum = [
            self.item(
                "Reddit r/Bogleheads",
                "Bond allocation during high yields",
                "Community discussion about duration, cash, and behavior.",
                "https://www.reddit.com/r/Bogleheads/example",
            )
        ]
        lines: list[str] = []

        dr.append_etf_research_sections(lines, items, forum)
        rendered = "\n".join(lines)

        for heading in [
            "市场 regime 是否变化",
            "资产配置影响",
            "量化策略影响",
            "A 股 / 港股专项",
            "论坛与社区 idea mining",
            "待验证假设",
        ]:
            self.assertIn(heading, rendered)
        self.assertIn("事实层", rendered)
        self.assertIn("不是事实结论", rendered)

    def test_article_detail_points_are_extracted_from_fetched_body_text(self) -> None:
        item = self.item(
            "Allocate Smartly",
            "Surfing the Equity Curve: Using Trend-Following to Switch Strategies On and Off",
            (
                "The article tests an equity-curve trend filter across tactical allocation strategies. "
                "It compares a 10-month moving average signal with a 12-month lookback rule and reports "
                "turnover, whipsaw risk, maximum drawdown, and missed rebound periods. "
                "The author warns that equity-curve filters can overfit because the switch is applied to "
                "the strategy's own historical performance rather than an independent market variable."
            ),
            "https://allocatesmartly.com/surfing-the-equity-curve",
        )

        points = dr.etf_article_detail_points(item, limit=3)
        lines: list[str] = []
        dr.append_article_detail_points(lines, item)
        rendered = "\n".join(lines)

        self.assertGreaterEqual(len(points), 2)
        self.assertIn("正文细节", rendered)
        self.assertIn("10-month", rendered)
        self.assertIn("12-month", rendered)
        self.assertIn("最大回撤", rendered)
        for label in ["风险控制：", "配置变量：", "信号定义：", "交易成本：", "回测/样本："]:
            self.assertNotIn(label, rendered)

    def test_generic_detail_fallbacks_are_not_rendered_as_summary(self) -> None:
        item = self.item(
            "Reddit r/portfolios",
            "Advice on Portfolio",
            "The post asks for advice on portfolio risk, correlation, and allocation.",
            "https://www.reddit.com/r/portfolios/comments/example/advice_on_portfolio/",
        )

        points = dr.etf_article_detail_points(item, limit=3)
        lines: list[str] = []
        dr.append_article_detail_points(lines, item)
        rendered = "\n".join(lines)

        self.assertEqual(points, [])
        self.assertNotIn("文章这一段", rendered)
        self.assertNotIn("正文抓取不足", rendered)
        self.assertNotIn("正文细节", rendered)

    def test_low_information_forum_items_are_skipped_after_link_enrichment_attempt(self) -> None:
        item = self.item(
            "Reddit r/portfolios",
            "Advice on Portfolio",
            "I need advice on my portfolio.",
            "https://www.reddit.com/r/portfolios/comments/example/advice_on_portfolio/",
        )
        lines: list[str] = []

        dr.append_etf_research_sections(lines, [], [item], [], [], "2026-05-15")
        rendered = "\n".join(lines)

        self.assertNotIn("RSS 摘要只提供有限信息", rendered)
        self.assertNotIn("当前可确认文章围绕", rendered)
        self.assertNotIn("**讨论事实**", rendered)
        self.assertIn("已从正文剔除", rendered)

    def test_article_detail_points_do_not_fall_back_to_english_excerpts(self) -> None:
        item = self.item(
            "Quantpedia",
            "The Attention Factor: The Link That Connects Crypto and Public Equity Markets",
            (
                "After regressing Bitcoin returns on global equity market returns and risk appetite proxies, "
                "a statistically significant residual connectedness remains, particularly for equities with "
                "revenue exposure to speculative participation such as Coinbase, Robinhood, DraftKings and "
                "sentiment-harvesting vehicles like the BUZZ Social Sentiment ETF. "
                "The findings advocate a spectrum-based assessment of speculative-sentiment exposure rather "
                "than a binary crypto yes/no allocation decision. "
                "The paper identifies a speculative cohort of marginal investors whose sentiment shifts propagate "
                "correlated price movements across BTC, 0DTE options, commission-free brokerages, and social-sentiment-driven equities."
            ),
            "https://quantpedia.com/the-attention-factor",
        )
        lines: list[str] = []

        dr.append_article_detail_points(lines, item)
        rendered = "\n".join(lines)

        self.assertNotIn("原文细节摘录", rendered)
        self.assertNotRegex(rendered, re.compile(r"[A-Za-z][A-Za-z ,'-]{80,}"))
        self.assertIn("投机情绪", rendered)
        self.assertIn("二元判断", rendered)

    def test_tactical_yield_article_details_are_summarized_in_chinese(self) -> None:
        item = self.item(
            "Allocate Smartly",
            "Meb Faber’s Tactical Yield, Simple and Intuitive",
            (
                "Backtested results from 1930 follow compared to a benchmark of 50% intermediate-term US Treasuries "
                "(IEF) and 50% US corporate bonds (LQD). Results are net of transaction costs. "
                "For intermediate-term US Treasuries, the initial 10-year yield has predicted about 86% of the total "
                "return over the subsequent 10 years. The strategy uses Tactical Yield to decide when T-Bills are "
                "more attractive than taking duration or credit risk."
            ),
            "https://allocatesmartly.com/meb-fabers-tactical-yield",
        )
        lines: list[str] = []

        dr.append_article_detail_points(lines, item)
        rendered = "\n".join(lines)

        self.assertNotIn("原文细节摘录", rendered)
        self.assertIn("1930", rendered)
        self.assertIn("50% IEF / 50% LQD", rendered)
        self.assertIn("10 年期初始收益率", rendered)
        self.assertIn("86%", rendered)

    def test_specific_article_heading_and_fact_capture_core_thesis(self) -> None:
        attention = self.item(
            "Quantpedia",
            "The Attention Factor: The Link That Connects Crypto and Public Equity Markets",
            "Speculative sentiment links BTC, 0DTE options, commission-free brokerages, and social-sentiment-driven equities.",
        )
        tactical_yield = self.item(
            "Allocate Smartly",
            "Meb Faber’s Tactical Yield, Simple and Intuitive",
            "The strategy compares T-Bills, Treasuries, and corporate bonds using yields and backtested results.",
        )

        self.assertIn("投机情绪", dr.etf_public_heading(attention.title, attention.summary))
        self.assertIn("共同风险因子", dr.etf_chinese_fact(attention))
        self.assertIn("Tactical Yield", dr.etf_public_heading(tactical_yield.title, tactical_yield.summary))
        self.assertIn("T-Bills", dr.etf_chinese_fact(tactical_yield))

    def test_commodity_article_preserves_risk_free_and_equity_comparison_numbers(self) -> None:
        item = self.item(
            "Quantpedia",
            "An Index of Commodity Futures Returns Since 1871",
            (
                "Commodity futures generated an average annual risk premium of 5.4% over the risk-free rate "
                "and a real return premium exceeding 6% per annum. Over the same long sample, equities earned "
                "about 6.8% over cash, providing a useful comparison for the commodity risk premium. "
                "Related article: Dual Momentum Allocation Between Physical Gold and Bitcoin reports Bitcoin returns."
            ),
        )
        lines: list[str] = []

        dr.append_article_detail_points(lines, item)
        rendered = "\n".join(lines)

        self.assertIn("5.4%", rendered)
        self.assertIn("无风险", rendered)
        self.assertIn("6%", rendered)
        self.assertIn("股票", rendered)
        self.assertIn("6.8%", rendered)
        self.assertIn("43%", rendered)
        self.assertNotIn("Bitcoin", rendered)
        self.assertNotIn("比特币", rendered)

    def test_attention_article_excludes_related_gold_momentum_article_noise(self) -> None:
        item = self.item(
            "Quantpedia",
            "The Attention Factor: The Link That Connects Crypto and Public Equity Markets",
            (
                "After regressing Bitcoin returns on global equity market returns and risk appetite proxies, "
                "a statistically significant residual connectedness remains. "
                "Related article: Dual Momentum Allocation Between Physical Gold and Bitcoin discusses gold momentum."
            ),
        )
        lines: list[str] = []

        dr.append_article_detail_points(lines, item)
        rendered = "\n".join(lines)

        self.assertIn("残余联动", rendered)
        self.assertNotIn("黄金与比特币", rendered)
        self.assertNotIn("动量配置关系", rendered)

    def test_gold_bitcoin_article_preserves_return_and_drawdown_scenarios(self) -> None:
        item = self.item(
            "Quantpedia",
            "Dual Momentum Allocation Between Physical Gold and Bitcoin (Digital Gold)",
            (
                "The dual momentum strategy produced an annualized return of 32.4% with a maximum drawdown of -28.6%. "
                "Bitcoin buy-and-hold delivered a higher annualized return of 48.1% but suffered a maximum drawdown of -83.4%. "
                "Gold alone produced an annualized return of 8.2% with a maximum drawdown of -21.5%."
            ),
        )
        lines: list[str] = []

        dr.append_article_detail_points(lines, item)
        rendered = "\n".join(lines)

        self.assertIn("双动量", rendered)
        self.assertIn("年化收益", rendered)
        self.assertIn("32.4%", rendered)
        self.assertIn("最大回撤", rendered)
        self.assertIn("-83.4%", rendered)
        self.assertIn("黄金", rendered)

    def test_quantocracy_enrichment_fetches_child_article_content(self) -> None:
        original_fetch = dr.fetch_bytes

        def fake_fetch(url: str, timeout: int = 30) -> bytes:
            if "quantocracy.com" in url:
                return (
                    b"<html><body><p>This is a summary of links recently featured on Quantocracy.</p>"
                    b"<a href='https://allocatesmartly.com/surfing-the-equity-curve/'>Surfing the Equity Curve</a>"
                    b"</body></html>"
                )
            if "allocatesmartly.com" in url:
                return (
                    b"<html><body><p>The child article tests an equity curve switch, reports annual return, "
                    b"Sharpe Ratio, turnover, maximum drawdown, and missed rebounds.</p></body></html>"
                )
            raise AssertionError(url)

        try:
            dr.fetch_bytes = fake_fetch
            item = self.item(
                "Quantocracy",
                "Recent Quant Links from Quantocracy as of 05/11/2026",
                "RSS only says this is a summary of links.",
                "https://quantocracy.com/recent-quant-links-from-quantocracy-as-of-05112026/",
            )
            enriched = dr.enrich_article_item(item)
        finally:
            dr.fetch_bytes = original_fetch

        self.assertIn("子链接", enriched.summary)
        self.assertIn("equity curve switch", enriched.summary)
        self.assertIn("maximum drawdown", enriched.summary)

    def test_forum_item_fetches_link_body_and_renders_full_summary(self) -> None:
        original_fetch = dr.fetch_bytes

        def fake_fetch(url: str, timeout: int = 30) -> bytes:
            if url.endswith(".json"):
                payload = [
                    {
                        "data": {
                            "children": [
                                {
                                    "data": {
                                        "title": "Advice on Portfolio",
                                        "selftext": (
                                            "I am 32 and currently hold 70% VTI, 20% VXUS, and 10% BND. "
                                            "I also keep six months of emergency cash in a money market fund. "
                                            "My main questions are whether the international allocation is too low, "
                                            "whether I should add more bonds before buying a house in three years, "
                                            "and how to coordinate this taxable account with my 401(k). "
                                            "Several replies say not to chase recent US stock outperformance, to keep the house down payment separate, "
                                            "and to place bond exposure preferentially in tax-advantaged accounts when possible."
                                        ),
                                    }
                                }
                            ]
                        }
                    }
                ]
                return json.dumps(payload).encode("utf-8")
            raise AssertionError(url)

        try:
            dr.fetch_bytes = fake_fetch
            item = self.item(
                "Reddit r/portfolios",
                "Advice on Portfolio",
                "RSS only says Advice on Portfolio.",
                "https://www.reddit.com/r/portfolios/comments/example/advice_on_portfolio/",
            )
            enriched = dr.enrich_article_item(item)
            lines: list[str] = []
            dr.append_etf_research_sections(lines, [], [enriched], [], [], "2026-05-15")
        finally:
            dr.fetch_bytes = original_fetch

        rendered = "\n".join(lines)

        self.assertIn("全文总结", rendered)
        self.assertIn("70% VTI、20% VXUS、10% BND", rendered)
        self.assertIn("六个月应急现金", rendered)
        self.assertIn("三年内买房", rendered)
        self.assertIn("401(k)", rendered)
        self.assertIn("Advice on Portfolio（投资组合建议请求）", rendered)
        self.assertNotIn("RSS only says", rendered)
        self.assertNotRegex(rendered, re.compile(r"[A-Za-z][A-Za-z ,'-]{80,}"))

    def test_forum_summary_does_not_emit_raw_english_fallback_sentences(self) -> None:
        item = self.item(
            "Reddit r/Bogleheads",
            "VNQ: Why not diversify into VNQ within tax-advantaged accounts?",
            (
                "I just wonder why, especially for those of us who do not own real estate, diversification under "
                "Boglehead philosophy is usually just limited to US/INTERNATIONAL/BONDS. "
                "Model portfolios from Rick Ferri and Paul Merriman include discreet funds for REITs."
            ),
            "https://www.reddit.com/r/Bogleheads/comments/example/vnq/",
        )

        lines: list[str] = []
        dr.append_etf_research_sections(lines, [], [item], [], [], "2026-05-15")
        rendered = "\n".join(lines)

        self.assertIn("全文总结", rendered)
        self.assertIn("REIT", rendered)
        self.assertIn("VNQ: Why not diversify into VNQ within tax-advantaged accounts?（为什么不在税优账户中用 VNQ 做 REIT 分散？）", rendered)
        self.assertNotRegex(rendered, re.compile(r"[A-Za-z][A-Za-z ,'-]{80,}"))

    def test_etf_article_title_displays_original_and_chinese_translation(self) -> None:
        item = self.item(
            "Quantpedia",
            "Dual Momentum Allocation Between Physical Gold and Bitcoin (Digital Gold)",
            (
                "The dual momentum strategy produced an annualized return of 32.4% with a maximum drawdown of -28.6%. "
                "Bitcoin buy-and-hold delivered a higher annualized return of 48.1% but suffered a maximum drawdown of -83.4%."
            ),
            "https://quantpedia.com/dual-momentum-allocation-between-physical-gold-and-bitcoin-digital-gold/",
        )
        scored = dr.rank_etf_research_items([item], limit=1)[0]
        lines: list[str] = []

        dr.append_scored_item(lines, scored, 1)
        rendered = "\n".join(lines)

        self.assertIn(
            "Dual Momentum Allocation Between Physical Gold and Bitcoin (Digital Gold)｜实物黄金与比特币（数字黄金）的双动量配置",
            rendered,
        )
        self.assertIn("标题：Dual Momentum Allocation Between Physical Gold and Bitcoin (Digital Gold)｜实物黄金与比特币（数字黄金）的双动量配置", rendered)
        self.assertNotIn("- 原文标题：", rendered)

    def test_bogleheads_forum_title_translation_and_detailed_summary(self) -> None:
        item = self.item(
            "Reddit r/Bogleheads",
            "Is it ok to have basically 100% of my retirement savings in stocks/funds and 0% bonds/treasuries if I'm far from retirement?",
            (
                "The original poster is far from retirement and asks whether keeping basically 100% of retirement savings "
                "in stocks/funds and 0% in bonds or Treasuries is reasonable. Replies discuss time horizon, risk tolerance, "
                "the role of bonds in reducing drawdowns, separating near-term cash needs from retirement assets, and using "
                "a glide path to add bonds as retirement approaches."
            ),
            "https://www.reddit.com/r/Bogleheads/comments/1tg3apt/is_it_ok_to_have_basically_100_of_my_retirement/",
        )
        lines: list[str] = []

        dr.append_etf_research_sections(lines, [], [item], [], [], "2026-05-15")
        rendered = "\n".join(lines)

        self.assertIn(
            "Is it ok to have basically 100% of my retirement savings in stocks/funds and 0% bonds/treasuries if I'm far from retirement?（离退休还很远，退休储蓄几乎 100% 股票/基金、0% 债券或国债是否可以？）",
            rendered,
        )
        self.assertIn("全文总结", rendered)
        self.assertIn("100% 股票/基金、0% 债券或国债", rendered)
        self.assertIn("离退休还很远", rendered)
        self.assertIn("风险承受能力", rendered)
        self.assertIn("临近退休", rendered)
        self.assertNotIn("已打开原帖链接并按正文内容归纳", rendered)

    def test_reddit_json_listing_preserves_engagement_for_forum_backfill(self) -> None:
        payload = {
            "data": {
                "children": [
                    {
                        "data": {
                            "subreddit": "ETFs",
                            "title": "SCHG vs QQQM for long term?",
                            "permalink": "/r/ETFs/comments/example/schg_vs_qqqm/",
                            "score": 184,
                            "num_comments": 71,
                            "created_utc": 1_700_000_000,
                            "selftext": "Portfolio allocation question comparing SCHG and QQQM.",
                        }
                    }
                ]
            }
        }

        item = dr.reddit_listing_items_from_payload(payload, "ETFs")[0]

        self.assertEqual(item.source, "Reddit r/ETFs（score/upvotes 184；comments/replies 71）")
        self.assertIn("https://www.reddit.com/r/ETFs/comments/example/schg_vs_qqqm/", item.url)
        self.assertGreater(dr.forum_engagement_score(item), 184)

    def test_forum_display_title_does_not_repeat_english_as_translation(self) -> None:
        item = self.item(
            "Reddit r/ETFs（score/upvotes 46；comments/replies 145）",
            "What’s a ETF you don’t plan on selling anytime soon?",
            "Discussion asks which ETF belongs in a long-term portfolio allocation.",
            "https://www.reddit.com/r/ETFs/comments/example/long_term_etf/",
        )

        self.assertEqual(
            dr.forum_display_title(item),
            "What’s a ETF you don’t plan on selling anytime soon?（你不打算长期卖出的 ETF 是哪只？）",
        )

    def test_reddit_source_marks_missing_engagement_fields(self) -> None:
        original = dr.reddit_thread_metadata
        try:
            dr.reddit_thread_metadata = lambda url: None
            self.assertEqual(
                dr.reddit_source_with_engagement("Reddit r/ETFs", "https://www.reddit.com/r/ETFs/comments/example/x/"),
                "Reddit r/ETFs（score/upvotes 未抓取；comments/replies 未抓取）",
            )
        finally:
            dr.reddit_thread_metadata = original

    def test_bogleheads_cash_to_short_term_bonds_title_translation(self) -> None:
        item = self.item(
            "Bogleheads Forum",
            'Personal Investments • Re: Time to Move "Cash" to Short Term Bonds?',
            (
                "The thread asks whether a cash allocation should be moved to short term bonds. "
                "Replies compare money market yields, short-term bond duration risk, tax treatment, liquidity, "
                "and whether the money is needed soon or belongs to the long-term portfolio."
            ),
            "https://www.bogleheads.org/forum/viewtopic.php?t=example",
        )
        lines: list[str] = []

        dr.append_etf_research_sections(lines, [], [item], [], [], "2026-06-03")
        rendered = "\n".join(lines)

        self.assertIn('Personal Investments • Re: Time to Move "Cash" to Short Term Bonds?（个人投资：是否该把现金转到短期债券？）', rendered)
        self.assertIn("现金仓位是否该转入短期债券", rendered)
        self.assertIn("税后收益/久期风险框架", rendered)
        self.assertNotRegex(rendered, re.compile(r"[A-Za-z][A-Za-z ,'-]{80,}"))

    def test_current_forum_digest_titles_have_chinese_display_labels(self) -> None:
        cases = {
            "voo vs. voo/vxus?": "VOO 单独持有，还是 VOO + VXUS 加入国际股票？",
            "What are the best bonds for high income earners?": "高收入者适合配置哪些债券？",
            "80k to invest + no debt how would you invest it?": "无债且有 8 万美元待投资资金，该如何配置？",
            "Rebuilding entire portfolio with boglehead strategy as primary influence.": "以 Bogleheads 策略为核心重建整个投资组合",
            "Rebuilding portfolio and need help (46m/44f) 3.5M investable assets": "46 岁/44 岁家庭重建 350 万美元可投资资产组合求助",
            "Help me analyzing this portfolio and suggestions for improvement": "请帮我分析这个投资组合并给出改进建议",
            "Unwind unrealized gains with taxable account": "应税账户里如何处理未实现资本利得？",
            "Where to invest next?": "下一步应该投向哪里？",
            "Target date fund ETF in brokerage account?": "应税券商账户里能否买目标日期基金 ETF？",
            "Need help consolidating my beginner portfolio": "新手投资组合需要合并整理，求帮助",
            "54 and Finally Waking up": "54 岁终于开始认真规划投资组合",
            "At what point did you diversify not only in VTSAX/VOO": "什么时候开始从 VTSAX/VOO 之外进一步分散配置？",
            "Will SPTM/SPHQ and active ETFs get forced into hype IPOs like SpaceX?": "SPTM/SPHQ 和主动 ETF 会被迫买入 SpaceX 这类热门 IPO 吗？",
            "Personal Investments • Re: $407K Net Worth, Large Cash Position, Looking for Advice on an Aggressive Investing Strategy": "个人投资：净资产 40.7 万美元、现金仓位较大，如何制定更积极的投资策略？",
        }

        for title, zh in cases.items():
            item = self.item("Reddit r/Bogleheads", title, "Portfolio allocation, bond, cash, taxable account and rebalance discussion.")
            rendered_title = dr.forum_display_title(item)
            self.assertIn(zh, rendered_title)
            self.assertNotEqual(rendered_title, title)

    def test_specific_fresh_forum_titles_can_render_as_lightweight_ideas(self) -> None:
        items = [
            self.item(
                "Reddit r/ETFs（score/upvotes 2；comments/replies 1）",
                "Will SPTM/SPHQ and active ETFs get forced into hype IPOs like SpaceX?",
                "",
                "https://www.reddit.com/r/ETFs/comments/example/sptm_sphq_spacex/",
            ),
            self.item(
                "Bogleheads.org Forum",
                "Personal Investments • Re: $407K Net Worth, Large Cash Position, Looking for Advice on an Aggressive Investing Strategy",
                "",
                "https://www.bogleheads.org/forum/viewtopic.php?t=500002",
            ),
            self.item(
                "Reddit r/Bogleheads（score/upvotes 1；comments/replies 0）",
                "Target date fund ETF in brokerage account?",
                "",
                "https://www.reddit.com/r/Bogleheads/comments/example/target_date_taxable/",
            ),
            self.item(
                "Reddit r/Bogleheads（score/upvotes 1；comments/replies 0）",
                "At what point did you diversify not only in VTSAX/VOO",
                "",
                "https://www.reddit.com/r/Bogleheads/comments/example/diversify_vtsax_voo/",
            ),
            self.item(
                "Reddit r/Bogleheads（score/upvotes 1；comments/replies 0）",
                "Need help consolidating my beginner portfolio",
                "",
                "https://www.reddit.com/r/Bogleheads/comments/example/beginner_consolidation/",
            ),
        ]
        lines: list[str] = []

        visible_count = dr.append_etf_research_sections(lines, [], items, [], [], "2026-06-03")
        rendered = "\n".join(lines)

        self.assertEqual(visible_count, 5)
        self.assertIn("SPTM/SPHQ 和主动 ETF 会被迫买入 SpaceX 这类热门 IPO 吗？", rendered)
        self.assertIn("个人投资：净资产 40.7 万美元、现金仓位较大，如何制定更积极的投资策略？", rendered)
        self.assertNotRegex(rendered, r"### \d+\. [^\n（]+$")

    def test_low_evidence_article_is_dropped_instead_of_hard_written_mapping(self) -> None:
        item = self.item(
            "Robot Wealth",
            "Everything Everywhere All at Once",
            "Subscribe to the newsletter. Related links mention factor momentum and portfolio risk.",
            "https://robotwealth.com/everything-everywhere-all-at-once/",
        )

        scored = dr.rank_etf_research_items([item], limit=5, require_evidence=True)

        self.assertEqual(scored, [])

    def test_active_etf_liquidity_title_is_not_mislabeled_as_capital_market_assumptions(self) -> None:
        item = self.item(
            "ETF Trends",
            "Goldman Sachs: Active ETFs Win the Liquidity Race",
            (
                "The article discusses active ETFs, liquidity, trading volume, bid-ask spreads, and how the ETF wrapper "
                "can provide intraday access compared with mutual funds. Sidebar text mentions yields and unrelated model portfolios."
            ),
            "https://www.etftrends.com/future-etfs-content-hub/goldman-sachs-active-etfs-win-liquidity-race/",
        )
        scored = dr.rank_etf_research_items([item], limit=5, require_evidence=True)
        lines: list[str] = []

        self.assertEqual(len(scored), 1)
        dr.append_scored_item(lines, scored[0], 1)
        rendered = "\n".join(lines)

        self.assertIn("主动 ETF 流动性", rendered)
        self.assertIn("买卖价差", rendered)
        self.assertNotIn("长期资本市场假设与估值", rendered)
        self.assertNotIn("最大回撤", rendered)

    def test_forum_renderer_keeps_high_engagement_lightweight_forum_backfill(self) -> None:
        forum_items = [
            self.item(
                "Reddit r/ETFs（score/upvotes 610；comments/replies 240）",
                "What’s a ETF you don’t plan on selling anytime soon?",
                "Community thread asks which ETF belongs in a long-term portfolio and why holders would keep it through market cycles.",
                "https://www.reddit.com/r/ETFs/comments/example/long_term_etf/",
            ),
            self.item(
                "Reddit r/Bogleheads（score/upvotes 420；comments/replies 130）",
                "Beginner Portfolio Help",
                "A beginner asks for portfolio help with broad ETF allocation, risk tolerance, and whether VTI and VXUS are enough.",
                "https://www.reddit.com/r/Bogleheads/comments/example/beginner_portfolio_help/",
            ),
            self.item(
                "Reddit r/portfolios（score/upvotes 315；comments/replies 98）",
                "Rate My Portfolio",
                "Poster asks the community to rate a portfolio allocation and discuss ETF diversification and rebalance choices.",
                "https://www.reddit.com/r/portfolios/comments/example/rate_my_portfolio/",
            ),
            self.item(
                "Reddit r/ETFs（score/upvotes 290；comments/replies 80）",
                "26M ETF Advice",
                "A 26-year-old asks for ETF advice on a core portfolio, contribution plan, and long-term risk level.",
                "https://www.reddit.com/r/ETFs/comments/example/26m_etf_advice/",
            ),
            self.item(
                "Reddit r/ETFs（score/upvotes 184；comments/replies 71）",
                "SCHG vs QQQM for long term?",
                "Question compares SCHG and QQQM for a long-term portfolio and asks about overlap, growth exposure, and rebalance decisions.",
                "https://www.reddit.com/r/ETFs/comments/example/schg_vs_qqqm/",
            ),
            self.item(
                "Reddit r/Bogleheads（score/upvotes 160；comments/replies 55）",
                "Best way to migrate multiple portfolios filled with crap to a Boglehead portfolio",
                "Thread asks how to migrate several messy portfolios into a Boglehead ETF allocation without creating tax and timing problems.",
                "https://www.reddit.com/r/Bogleheads/comments/example/migrate_to_boglehead/",
            ),
        ]
        lines: list[str] = []

        visible_count = dr.append_etf_research_sections(lines, [], forum_items, [], [], "2026-05-25")
        rendered = "\n".join(lines)

        self.assertEqual(visible_count, 6)
        self.assertIn("线索摘要", rendered)
        self.assertIn("What’s a ETF you don’t plan on selling anytime soon?（你不打算长期卖出的 ETF 是哪只？）", rendered)
        self.assertIn("Beginner Portfolio Help（新手投资组合需要合并整理，求帮助）", rendered)
        self.assertIn("Rate My Portfolio（请评价我的投资组合）", rendered)
        self.assertIn("26M ETF Advice", rendered)
        self.assertIn("SCHG vs QQQM for long term?（长期持有选 SCHG 还是 QQQM？）", rendered)
        self.assertIn("Best way to migrate multiple portfolios filled with crap to a Boglehead portfolio（如何把多个混乱组合迁移成 Bogleheads 风格组合）", rendered)
        self.assertIn("论坛补充入正文数量：6", rendered)
        self.assertNotRegex(rendered, re.compile(r"[A-Za-z][A-Za-z ,'-]{100,}"))

    def test_forum_title_translation_is_specific_not_generic_topic_label(self) -> None:
        item = self.item(
            "Reddit r/portfolios（score/upvotes 45；comments/replies 75）",
            "Does this make sense? Overengineered portfolio, or a robust and rational one?",
            "Poster asks whether a multi-ETF portfolio allocation is overengineered or robust, rational, and suitable for long-term rebalancing.",
            "https://www.reddit.com/r/portfolios/comments/example/overengineered_portfolio/",
        )
        lines: list[str] = []

        visible_count = dr.append_etf_research_sections(lines, [], [item], [], [], "2026-05-26")
        rendered = "\n".join(lines)

        self.assertEqual(visible_count, 1)
        self.assertIn(
            "Does this make sense? Overengineered portfolio, or a robust and rational one?（这样配置合理吗？组合是否过度设计，还是稳健理性的方案？）",
            rendered,
        )
        self.assertNotIn("Does this make sense? Overengineered portfolio, or a robust and rational one?（资产配置/ETF论坛讨论）", rendered)

    def test_forum_title_translation_covers_current_lightweight_backfill_titles(self) -> None:
        cases = {
            "Would SPMO + VFMO as a U.S. portfolio core make sense to add a little risk instead of the typical VOO as the core?": "用 SPMO + VFMO 作为美股核心、比典型 VOO 核心多承担一点风险是否合理？",
            "Advice on how to improve my portfolio (25M)": "25 岁男性如何改进当前投资组合？",
            "18 with $1,000; what do I do?": "18 岁有 1,000 美元，应该怎么开始投资？",
            "Rate the portfolio and give advice pls": "请评价这个投资组合并给些建议",
            "Portfolio age 33": "33 岁投资组合求评",
            "Roth vs Traditional 401k": "Roth 401(k) 与传统 401(k) 如何选择？",
            "Sold everything to rebalance my ETF portfolio.": "为重新平衡 ETF 组合卖出全部持仓是否合适？",
            "Portfolio Feedback for a 31 year old": "31 岁投资组合反馈求评",
            "Managing portfolio investments over time - signals, intuition, strategies?": "如何长期管理组合投资：信号、直觉还是策略？",
            "Bogleheads living in South Korea - How are we doing?": "生活在韩国的 Bogleheads：我们的配置做得怎么样？",
            "my parents have paid their financial advisor roughly $47k in fees over 15 years for market returns": "父母 15 年来为接近市场回报向财务顾问支付约 4.7 万美元费用",
            "My sister has been an Edward Jones broker for more than two decades and not one family member has even $1 invested there.": "姐姐做了二十多年 Edward Jones 经纪人，但家人没有一美元投在那里",
            "Real estate or S&P 500. Honestly, what‘s the better investment for you?": "房地产还是标普 500：对你来说哪个投资更好？",
            "S&P 500 Index Not So Diversified": "标普 500 指数是否并没有那么分散？",
            "PSA- Mega IPOs are nothing to worry about as an index investor": "提醒：作为指数投资者不必过度担心大型 IPO",
            "People who bought stocks early when they were still risky, unpopular, or getting hated on, what made you buy?": "早期买入仍有风险、不受欢迎或被嫌弃股票的人，当初为什么买？",
            "Protecting ourselves from SpaceX IPO": "如何防范 SpaceX IPO 对指数组合的影响？",
            "SpaceX IPO and NASDAQ violating its own methodology": "SpaceX IPO 与纳斯达克是否违背自身指数方法论",
            "Should I stop contributing to 401k and IRA": "我是否应该停止向 401(k) 和 IRA 供款？",
            "how much cash is too much?": "现金持有多少算太多？",
            "What would the collapse of the Bond Market mean for stocks?": "债券市场崩溃对股票意味着什么？",
            "I don't want to rebalance, what's your take?": "我不想再平衡组合，你怎么看？",
            "19yo incoming college freshman; doubled money earned from internship last summer within first year of investing": "19 岁准大学新生：入市第一年把去年实习收入翻倍",
            "How’s the portfolio looking now? 26M": "26 岁男性：现在这个投资组合看起来怎么样？",
            "How to get over the FOMO from all the other investing subreddits?": "如何克服其他投资社区带来的错失恐惧？",
            "What should i Change or upgrade on my portfolio?": "我的投资组合应该调整或升级什么？",
            "So where do I convert to bonds?": "我应该在什么位置转向债券？",
            "Re-balance question": "关于投资组合再平衡的问题",
            "Age 22 any recommendations?": "22 岁投资组合有什么建议？",
            "The Latest Morningstar Report Shows How to Invest in 2026": "Morningstar 最新报告：2026 年应如何投资？",
            "Personal Investments • Re: Dividend investing or not?": "个人投资：是否应该做股息投资？",
            "Personal Investments • Re: When should I create tips ladder?Now or wait": "个人投资：什么时候建立 TIPS 阶梯？现在还是等待？",
            "Personal Investments ? Re: When should I create tips ladder?Now or wait": "个人投资：什么时候建立 TIPS 阶梯？现在还是等待？",
            "22M taxable brokerage portfolio review, inquisitive about barbell growth and macro diversifier strategy": "22 岁男性应税券商账户组合求评：杠铃式成长与宏观分散策略是否合适？",
            "Recently Opened Self-Managed Brokerage and my Roth IRA Strategy": "新开自主管理券商账户与 Roth IRA 策略求评",
            "Help diversify portfolio": "如何让投资组合更加分散？",
            "How long until I hit a million": "我还要多久才能达到 100 万美元？",
            "New in ETF. Looking for advice": "ETF 新手寻求投资建议",
            "Investment allocations": "投资配置比例讨论",
            "Roast/Help my portfolio. Not great at this.": "请吐槽或帮我改进投资组合：我不太擅长配置",
            "30m and i feel like like im falling behind": "30 岁男性觉得自己的投资进度落后",
            "Allocation across account types": "不同账户类型之间如何分配资产",
            "Rate my Portfolio2 months in": "入市两个月，请评价我的投资组合",
            "Started a taxable investment account (Feedback appreciated)": "刚开始应税投资账户，欢迎反馈",
            "Do some of you completely disregard the bond portion of a portfolio? All equities?": "你们中有人完全忽略组合中的债券部分、全仓股票吗？",
            "Portfolio planning simplest": "最简单的投资组合规划",
            "Any suggestions? Taxable brokerage": "应税券商账户有什么建议？",
            "Rate this portfolio": "请评价我的投资组合",
            "Who is still doing “VTI and Chill” ?": "还有谁在坚持“VTI and Chill”？",
            "I built WealthPie because my investing spreadsheet got way too complicated": "我做了 WealthPie，因为我的投资表格变得过于复杂",
            "Moving on from AUM and rebalancing": "不再依赖 AUM 顾问后如何再平衡",
            "Rate my portfolio please": "请评价我的投资组合",
            "Personal Investments • Re: Retire at 55 heavy in taxable": "个人投资：55 岁退休且应税账户占比较高",
            "Add SPMO or FMTM?": "添加 SPMO 还是 FMTM？",
            "Investment suggestion": "投资建议请求",
            "Employer just reversed 2026 Safe Harbor matches via negative Controbitions": "雇主通过负向缴款撤回了 2026 年 Safe Harbor 匹配缴款",
            "Vanguard Advised Redundancy": "Vanguard 顾问服务是否重复多余",
        }

        for title, expected_translation in cases.items():
            with self.subTest(title=title):
                item = self.item(
                    "Reddit r/ETFs（score/upvotes 40；comments/replies 80）",
                    title,
                    "Forum thread discusses portfolio allocation, ETF core holdings, account choice, risk tolerance, and rebalance decisions.",
                    "https://www.reddit.com/r/ETFs/comments/example/current_backfill/",
                )
                rendered_title = dr.forum_display_title(item)

                self.assertEqual(rendered_title, f"{title}（{expected_translation}）")
                self.assertRegex(rendered_title, r"（[^）]*[\u4e00-\u9fff][^）]*）")
                self.assertNotRegex(rendered_title, r"（[^）]*相关问题）")
                for generic_heading in dr.GENERIC_FORUM_HEADINGS:
                    self.assertNotIn(f"（{generic_heading}）", rendered_title)

    def test_forum_section_rendering_does_not_use_generic_labels_as_title_translations(self) -> None:
        items = [
            self.item(
                "Reddit r/portfolios（score/upvotes 40；comments/replies 80）",
                title,
                "Forum thread discusses portfolio allocation, ETF core holdings, account choice, risk tolerance, taxable account, 401k, IRA, and rebalance decisions.",
                f"https://www.reddit.com/r/portfolios/comments/example/{idx}/",
            )
            for idx, title in enumerate(
                [
                    "Personal Investments • Re: Retire at 55 heavy in taxable",
                    "Add SPMO or FMTM?",
                    "Investment suggestion",
                    "Employer just reversed 2026 Safe Harbor matches via negative Controbitions",
                    "Vanguard Advised Redundancy",
                    "Moving on from AUM and rebalancing",
                    "I built WealthPie because my investing spreadsheet got way too complicated",
                    "Rate this portfolio",
                    "Any suggestions? Taxable brokerage",
                    "Transition to 3 Fund Portfolio",
                ],
                1,
            )
        ]
        lines: list[str] = []

        visible_count = dr.append_etf_research_sections(lines, [], items, [], [], "2026-06-02")
        rendered = "\n".join(lines)

        self.assertEqual(visible_count, 10)
        self.assertIn("Personal Investments • Re: Retire at 55 heavy in taxable（个人投资：55 岁退休且应税账户占比较高）", rendered)
        self.assertIn("Add SPMO or FMTM?（添加 SPMO 还是 FMTM？）", rendered)
        self.assertIn("Employer just reversed 2026 Safe Harbor matches via negative Controbitions（雇主通过负向缴款撤回了 2026 年 Safe Harbor 匹配缴款）", rendered)
        self.assertIn("Vanguard Advised Redundancy（Vanguard 顾问服务是否重复多余）", rendered)
        self.assertNotRegex(rendered, r"（[^）]*相关问题）")
        for generic_heading in dr.GENERIC_FORUM_HEADINGS:
            self.assertNotIn(f"（{generic_heading}）", rendered)
            self.assertNotIn(f"围绕“{generic_heading}”", rendered)

    def test_forum_section_rendering_does_not_use_generic_labels_as_title_translations_for_reported_titles(self) -> None:
        items = [
            self.item(
                "Reddit r/portfolios（score/upvotes 40；comments/replies 80）",
                title,
                "Forum thread discusses portfolio allocation, ETF core holdings, account choice, risk tolerance, taxable account, 401k, IRA, and rebalance decisions.",
                f"https://www.reddit.com/r/portfolios/comments/reported/{idx}/",
            )
            for idx, title in enumerate(
                [
                    "Do some of you completely disregard the bond portion of a portfolio? All equities?",
                    "Portfolio planning simplest",
                    "Any suggestions? Taxable brokerage",
                    "Rate this portfolio",
                    "Who is still doing “VTI and Chill” ?",
                    "I built WealthPie because my investing spreadsheet got way too complicated",
                    "Moving on from AUM and rebalancing",
                    "Rate my portfolio please",
                    "Transition to 3 Fund Portfolio",
                    "Lazy Portfolios",
                ],
                1,
            )
        ]
        lines: list[str] = []

        visible_count = dr.append_etf_research_sections(lines, [], items, [], [], "2026-05-30")
        rendered = "\n".join(lines)

        self.assertEqual(visible_count, 10)
        self.assertIn(
            "Do some of you completely disregard the bond portion of a portfolio? All equities?（你们中有人完全忽略组合中的债券部分、全仓股票吗？）",
            rendered,
        )
        self.assertIn("Who is still doing “VTI and Chill” ?（还有谁在坚持“VTI and Chill”？）", rendered)
        self.assertIn("Moving on from AUM and rebalancing（不再依赖 AUM 顾问后如何再平衡）", rendered)
        self.assertIn("Transition to 3 Fund Portfolio（过渡到三基金组合）", rendered)
        self.assertNotRegex(rendered, r"（[^）]*相关问题）")
        for generic_heading in dr.GENERIC_FORUM_HEADINGS:
            self.assertNotIn(f"（{generic_heading}）", rendered)
            self.assertNotIn(f"围绕“{generic_heading}”", rendered)

    def test_forum_selector_does_not_recover_minimum_with_recent_history_duplicates(self) -> None:
        original_now_bj = dr.now_bj
        dr.now_bj = lambda: dr.datetime(2026, 5, 28, 7, 0, tzinfo=dr.BJ)
        items = [
            self.item(
                "Reddit r/portfolios（score/upvotes 40；comments/replies 80）",
                f"Portfolio Feedback for a {age} year old",
                "Post gives a portfolio allocation and asks for risk tolerance, ETF core, and rebalance feedback.",
                f"https://www.reddit.com/r/portfolios/comments/example/portfolio_feedback_{age}/",
            )
            for age in range(31, 41)
        ]
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history_dir = tmp_path / "digest_history"
            history_dir.mkdir()
            (history_dir / "etf.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "sent_date": "2026-05-27",
                                "source": item.source,
                                "title": item.title,
                                "url": item.url,
                            }
                            for item in items[:6]
                        ]
                    }
                ),
                encoding="utf-8",
            )
            os.chdir(tmp_path)
            try:
                picked = dr.select_etf_forum_items(items, limit=10)
            finally:
                os.chdir(original_cwd)
                dr.now_bj = original_now_bj

        self.assertEqual([item.title for item in picked], [item.title for item in items[6:]])

    def test_recent_forum_history_backfill_items_are_not_reselected(self) -> None:
        original_cwd = Path.cwd()
        original_now_bj = dr.now_bj
        dr.now_bj = lambda: dr.datetime(2026, 6, 4, 7, 0, tzinfo=dr.BJ)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history_dir = tmp_path / "digest_history"
            history_dir.mkdir()
            (history_dir / "etf.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "sent_date": "2026-06-03",
                                "source": "Reddit r/Bogleheads",
                                "title": f"Portfolio Feedback for a {age} year old",
                                "url": f"https://www.reddit.com/r/Bogleheads/comments/example/history_{age}/",
                            }
                            for age in range(31, 41)
                        ]
                    }
                ),
                encoding="utf-8",
            )
            os.chdir(tmp_path)
            try:
                backfill = dr.forum_history_backfill_items(limit=10)
                picked = dr.select_etf_forum_items(backfill, limit=10)
            finally:
                os.chdir(original_cwd)
                dr.now_bj = original_now_bj

        self.assertEqual(picked, [])
        self.assertTrue(all(dr.renderable_forum_item(item) for item in backfill))

    def test_same_day_forum_supplement_keeps_new_today_items_but_not_prior_duplicates(self) -> None:
        original_cwd = Path.cwd()
        original_now_bj = dr.now_bj
        dr.now_bj = lambda: dr.datetime(2026, 6, 4, 7, 0, tzinfo=dr.BJ)
        picked = [
            self.item(
                "Reddit r/Bogleheads",
                f"Portfolio allocation fresh picked {idx}",
                "Thread discusses ETF core holdings, risk tolerance, bond allocation, and rebalance decisions.",
                f"https://www.reddit.com/r/Bogleheads/comments/example/picked_{idx}/",
            )
            for idx in range(4)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history_dir = tmp_path / "digest_history"
            history_dir.mkdir()
            (history_dir / "etf.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "sent_date": "2026-06-03",
                                "source": "Reddit r/Bogleheads",
                                "title": "voo vs. voo/vxus?",
                                "url": "https://www.reddit.com/r/Bogleheads/comments/example/voo_vxus/",
                            },
                            {
                                "sent_date": "2026-06-04",
                                "source": "Reddit r/Bogleheads",
                                "title": "voo vs. voo/vxus?",
                                "url": "https://www.reddit.com/r/Bogleheads/comments/example/voo_vxus/",
                            },
                            {
                                "sent_date": "2026-06-04",
                                "source": "Reddit r/Bogleheads",
                                "title": "Target date fund ETF in brokerage account?",
                                "url": "https://www.reddit.com/r/Bogleheads/comments/example/target_date/",
                            },
                            {
                                "sent_date": "2026-06-04",
                                "source": "Reddit r/Bogleheads",
                                "title": "Need help consolidating my beginner portfolio",
                                "url": "https://www.reddit.com/r/Bogleheads/comments/example/beginner_consolidating/",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            os.chdir(tmp_path)
            try:
                supplemented = dr.supplement_forum_items_with_same_day_new_history(picked, minimum=6, limit=10)
            finally:
                os.chdir(original_cwd)
                dr.now_bj = original_now_bj

        titles = [item.title for item in supplemented]
        self.assertGreaterEqual(len(titles), 6)
        self.assertIn("Target date fund ETF in brokerage account?", titles)
        self.assertNotIn("voo vs. voo/vxus?", titles)

    def test_same_day_forum_supplement_dedupes_same_title_with_different_reply_urls(self) -> None:
        original_cwd = Path.cwd()
        original_now_bj = dr.now_bj
        dr.now_bj = lambda: dr.datetime(2026, 6, 4, 7, 0, tzinfo=dr.BJ)
        duplicate_title = (
            "Personal Investments • Re: $407K Net Worth, Large Cash Position, "
            "Looking for Advice on an Aggressive Investing Strategy"
        )
        picked = [
            self.item(
                "Bogleheads.org Forum",
                duplicate_title,
                "Thread discusses a large cash position, aggressive investing strategy, ETF core holdings, and rebalance decisions.",
                "https://www.bogleheads.org/forum/viewtopic.php?p=8780009#p8780009",
            ),
            *[
                self.item(
                    "Reddit r/Bogleheads",
                    f"Portfolio allocation fresh picked {idx}",
                    "Thread discusses ETF core holdings, risk tolerance, bond allocation, and rebalance decisions.",
                    f"https://www.reddit.com/r/Bogleheads/comments/example/picked_dedupe_{idx}/",
                )
                for idx in range(3)
            ],
        ]
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history_dir = tmp_path / "digest_history"
            history_dir.mkdir()
            (history_dir / "etf.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "sent_date": "2026-06-04",
                                "source": "Reddit r/Bogleheads",
                                "title": "Target date fund ETF in brokerage account?",
                                "url": "https://www.reddit.com/r/Bogleheads/comments/example/target_date_dedupe/",
                            },
                            {
                                "sent_date": "2026-06-04",
                                "source": "Bogleheads.org Forum",
                                "title": duplicate_title,
                                "url": "https://www.bogleheads.org/forum/viewtopic.php?p=8780005#p8780005",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            os.chdir(tmp_path)
            try:
                supplemented = dr.supplement_forum_items_with_same_day_new_history(picked, minimum=5, limit=10)
            finally:
                os.chdir(original_cwd)
                dr.now_bj = original_now_bj

        titles = [item.title for item in supplemented]
        self.assertEqual(titles.count(duplicate_title), 1)
        self.assertIn("Target date fund ETF in brokerage account?", titles)

    def test_extend_forum_items_to_minimum_uses_history_after_selector_stays_sparse(self) -> None:
        picked = [
            self.item(
                "Reddit r/portfolios（score/upvotes 未抓取；comments/replies 未抓取）",
                "Portfolio Feedback for a 31 year old",
                "Post gives a portfolio allocation and asks for risk tolerance, ETF core, and rebalance feedback.",
                "https://www.reddit.com/r/portfolios/comments/example/live_31/",
            )
        ]
        history_items = [
            self.item(
                "Reddit r/Bogleheads",
                f"Portfolio Feedback for a {age} year old",
                dr.forum_history_backfill_summary(f"Portfolio Feedback for a {age} year old"),
                f"https://www.reddit.com/r/Bogleheads/comments/example/history_extend_{age}/",
            )
            for age in range(32, 42)
        ]

        extended = dr.extend_forum_items_to_minimum(picked, history_items, minimum=dr.ETF_MIN_FORUM_ITEMS, limit=10)

        self.assertGreaterEqual(len(extended), dr.ETF_MIN_FORUM_ITEMS)
        self.assertLessEqual(len(extended), 10)
        self.assertEqual(extended[0].title, picked[0].title)

    def test_ensure_non_reddit_forum_mix_keeps_external_forums_when_available(self) -> None:
        picked = [
            self.item(
                "Reddit r/ETFs（score/upvotes 100；comments/replies 50）",
                f"Portfolio Feedback for a {age} year old",
                "Post gives a portfolio allocation and asks for risk tolerance, ETF core, and rebalance feedback.",
                f"https://www.reddit.com/r/ETFs/comments/example/reddit_{age}/",
            )
            for age in range(31, 41)
        ]
        candidates = [
            self.item(
                "Bogleheads.org Forum",
                "Bogleheads living in South Korea - How are we doing?",
                "Forum thread discusses Bogleheads portfolio allocation, ETF core holdings, and rebalancing.",
                "https://www.bogleheads.org/forum/viewtopic.php?t=500001",
            ),
            self.item(
                "Bogleblog Best of Bogleheads Forum",
                "Transition to 3 Fund Portfolio",
                "Curated Bogleheads forum index item about portfolio allocation, ETF core holdings, and rebalancing.",
                "https://www.bogleheads.org/forum/viewtopic.php?t=407430",
            ),
        ]

        mixed = dr.ensure_non_reddit_forum_mix(picked, candidates, min_non_reddit=2, limit=10)

        self.assertEqual(len(mixed), 10)
        self.assertGreaterEqual(sum(1 for item in mixed if "reddit" not in item.source.lower()), 2)
        self.assertIn("Bogleheads.org Forum", {item.source for item in mixed})
        self.assertIn("Bogleblog Best of Bogleheads Forum", {item.source for item in mixed})

    def test_generic_forum_post_is_skipped_without_thread_specific_summary(self) -> None:
        item = self.item(
            "Reddit r/ETFs",
            "ETFs to invest in",
            "Replies mention risk tolerance and retirement in passing, but no concrete portfolio, tickers, cash need, or allocation question.",
            "https://www.reddit.com/r/ETFs/comments/example/etfs_to_invest_in/",
        )
        lines: list[str] = []

        dr.append_etf_research_sections(lines, [], [item], [], [], "2026-05-18")
        rendered = "\n".join(lines)

        self.assertNotIn("ETFs to invest in", rendered)
        self.assertIn("已从正文剔除", rendered)


    def test_forum_renderer_returns_actual_visible_count(self) -> None:
        item = self.item(
            "Reddit r/ETFs",
            "SCHG vs QQQM for long term?",
            (
                "The post compares portfolio allocation choices between SCHG and QQQM. "
                "Replies discuss whether the overlap creates concentrated growth exposure, "
                "whether the choice belongs in a long-term core portfolio, and how this affects rebalance decisions."
            ),
            "https://www.reddit.com/r/ETFs/comments/example/schg_vs_qqqm/",
        )
        lines: list[str] = []

        visible_count = dr.append_etf_research_sections(lines, [], [item], [], [], "2026-05-18")

        self.assertEqual(visible_count, 1)


if __name__ == "__main__":
    unittest.main()
