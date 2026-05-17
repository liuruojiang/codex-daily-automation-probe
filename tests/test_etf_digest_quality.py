from __future__ import annotations

import sys
import unittest
import re
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import daily_reports as dr  # noqa: E402


class EtfDigestQualityTests(unittest.TestCase):
    def item(self, source: str, title: str, summary: str = "", url: str = "https://example.com/x") -> dr.Item:
        return dr.Item(source=source, title=title, url=url, published="2026-05-15T12:00:00+00:00", summary=summary)

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


if __name__ == "__main__":
    unittest.main()
