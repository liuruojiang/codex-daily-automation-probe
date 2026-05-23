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

    def test_etf_forum_selector_backfills_renderable_threads_with_short_dedupe(self) -> None:
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

        self.assertEqual([x.url for x in picked], [renderable.url])

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
        self.assertIn("投资组合持仓清理求建议", rendered)
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
        self.assertIn("税优账户中是否应单列 REIT/VNQ", rendered)
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
