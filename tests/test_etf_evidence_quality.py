from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import daily_reports as dr


# Public original abstract excerpt, arXiv v1 submitted 2026-08-09 01:45 UTC.
# Source: https://arxiv.org/abs/2608.08405
# This is evidence for an abstract-level note, not an independent confirmation
# of the paper's claims or proof that it was available in an earlier RSS run.
CROWDING_ABSTRACT = (
    "How much capital a trading strategy can absorb before its edge disappears is a causal question "
    "about how much is deployed, but it is answered with observational proxies that rest on incompatible assumptions. "
    "We ask what experiment would answer it instead, and show that two features of the problem interact to constrain any answer. "
    "Deployed capital erodes the edge gradually, so a trial of fixed length measures less than the eventual effect; "
    "and parallel implementations of one strategy trade the same securities, so they are not independent units. "
    "Comparing implementations on the same date removes market-wide shocks, which is what makes the comparison credible. "
    "But the crowding created by the strategy's own accumulated position is common to those implementations too, "
    "and an arbitrary date effect absorbs it exactly: the comparison that makes the experiment robust is the one "
    "that prevents it from measuring the crowding capacity is about."
)


class EvidenceQualityTests(unittest.TestCase):
    def item(self, text=CROWDING_ABSTRACT, title="Robustness or Crowding: Experimental Design for Trading Strategy Capacity"):
        return dr.Item("arXiv q-fin.PM", title, "https://arxiv.org/abs/2608.08405", "2026-08-09T01:45:00Z", text)

    def test_real_crowding_abstract_survives_without_finance_keyword_sentences(self):
        self.assertEqual(sum(dr.detail_sentence_score(s) >= 8 for s in dr.split_article_sentences(CROWDING_ABSTRACT)), 0)
        self.assertTrue(dr.etf_has_enough_summary_evidence(self.item()))

    def test_evidence_does_not_depend_on_detail_ranking_or_known_title(self):
        with patch.object(dr, "detail_sentence_score", side_effect=AssertionError("ranking is not evidence")):
            self.assertTrue(dr.etf_has_enough_summary_evidence(self.item(title="Unseen methodological analysis")))

    def test_noise_tokens_do_not_match_inside_research_words(self):
        text = (
            "We examine experimental design in settings where several participants share the same underlying process. "
            "The design inference problem requires separate observations to identify the influence of each participant."
        )
        self.assertTrue(dr.etf_has_enough_summary_evidence(self.item(text)))

    def test_unrelated_readable_text_is_evidence_not_a_relevance_approval(self):
        text = (
            "The botanists describe how the leaves were photographed under different lighting conditions in the laboratory. "
            "They explain why the resulting images cannot be compared without first accounting for the changes in illumination."
        )
        self.assertTrue(dr.etf_has_enough_summary_evidence(self.item(text)))

    def test_ads_login_navigation_and_numeric_padding_are_not_evidence(self):
        examples = [
            "Subscribe to our premium newsletter for exclusive portfolio risk and allocation research every morning. "
            "Sign up for our members account today to unlock the best investment ideas and historical backtest results.",
            "This article is available to subscribers with a registered account for our investment research service. "
            "Log in to continue reading our detailed research on portfolio allocation and systematic trading strategies.",
            "<nav>" + CROWDING_ABSTRACT + "</nav><footer>" + CROWDING_ABSTRACT + "</footer>",
            "<script>" + CROWDING_ABSTRACT + "</script>",
            ("portfolio allocation risk 2026 12% 15% 18% 25% 30% 45% 60% 75% 90% 100%. " * 6),
            "Portfolio allocation covariance rebalance uncertainty duration fixed-income equities systematic bonds. "
            "Historical performance profitability significance backtests turnover drawdowns capacity experiment implementation.",
            "",
        ]
        for text in examples:
            with self.subTest(text=text[:50]):
                self.assertFalse(dr.etf_has_enough_summary_evidence(self.item(text)))

    def test_repeated_sentence_and_near_duplicate_do_not_manufacture_evidence(self):
        sentence = "The experiment compares separate implementations of a common trading process and explains why they cannot be treated as independent observations."
        for text in (sentence * 4, sentence + " " + sentence.replace("The experiment", "Our experiment")):
            self.assertFalse(dr.etf_has_enough_summary_evidence(self.item(text)))

    def test_one_genuine_sentence_plus_padded_ad_does_not_pass(self):
        first = CROWDING_ABSTRACT.split(". ")[0] + ". "
        self.assertFalse(dr.etf_has_enough_summary_evidence(self.item(first + "Subscribe for allocation risk research with our premium membership and unlock detailed backtest performance results. ")))

    def test_a_valid_article_can_contain_a_discarded_subscription_footer(self):
        self.assertTrue(dr.etf_has_enough_summary_evidence(self.item(CROWDING_ABSTRACT + " Subscribe to our newsletter today.")))

    def test_chinese_sentences_without_spaces_are_readable(self):
        text = (
            "研究者指出，同一策略的不同账户可能同时买卖相同证券，因此账户之间的成交影响并非彼此独立；直接比较账户表现不能识别所有账户共同造成的市场冲击。"
            "这篇方法论文章进一步区分个别账户增加投入的影响与整个策略扩大规模的影响，并讨论实验应该怎样设置对照组以及需要保留哪些时间变化，才能回答不同层次的问题。"
            "作者给出的是识别条件和实验设计边界，而不是可以直接用于实际交易的容量数值；正式采用前仍须检查样本是否满足这些条件并独立核对成本假设。"
        )
        self.assertTrue(dr.etf_has_enough_summary_evidence(self.item(text)))


if __name__ == "__main__":
    unittest.main()
