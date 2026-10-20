import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import etf_content_cards as cards
import etf_material_kind as kinds
import etf_shadow_run as shadow

PROSE = ("The study reports a 12% historical return before transaction costs and uses a limited sample of portfolio observations. "
         "The authors explain that these observations cannot establish future performance and that implementation costs have not been tested. ")
TIMELINE = ("00:00 - Introduction to investment strategy and the changing responsibilities of managers across different pension schemes. "
            "05:20 - Why returns vary across markets and how institutions describe their approach to long term investing. "
            "12:30 - How managers allocate risk and communicate portfolio construction decisions to their investment committees. "
            "20:00 - Questions about future performance, operational costs and the selection of external investment partners.")


def material(text, url="https://example.org/study"):
    return cards.make_material(title="Investment discussion", url=url, source="synthetic", published="", paragraphs=[text])


class MaterialKindTests(unittest.TestCase):
    def test_long_directory_is_still_not_substantive_source(self):
        m = material("Please subscribe and rate this episode. " + TIMELINE)
        self.assertTrue(cards.basic_ready(m))
        self.assertFalse(cards.ready(m))
        with self.assertRaisesRegex(ValueError, "source text"):
            cards.request_payload(m, "generator")
        profile = kinds.classify(m)
        self.assertFalse(profile["eligible"])
        self.assertEqual(profile["kind"], "timeline_dominant")
        self.assertEqual(profile["metrics"]["timecodes"], 4)

    def test_promotion_with_short_chapters_is_blocked_even_if_chapters_not_majority(self):
        m = material("Please subscribe and buy our guide " * 15 + TIMELINE)
        self.assertEqual(kinds.classify(m)["kind"], "mixed_timeline")

    def test_timestamped_transcript_with_long_discussion_is_allowed(self):
        m = material("00:00 Alice: " + PROSE * 3 + "05:20 Bob: " + PROSE * 3 + "12:30 Alice: " + PROSE * 3)
        self.assertTrue(kinds.classify(m)["eligible"])
        self.assertEqual(m["evidence_level"], "captured_excerpt")

    def test_article_with_navigation_and_substantial_prose_is_allowed(self):
        m = material(PROSE * 6 + TIMELINE)
        self.assertTrue(kinds.classify(m)["eligible"])

    def test_chinese_substantive_transcript_is_not_rejected_for_timecodes(self):
        paragraph = ("该研究报告历史观察的结果，并提醒读者历史表现不能保证未来表现，其中包括不同市场环境中的风险变化以及组合构建方法所依赖的假设条件，需要进行谨慎核对。"
                     "其分析限定于给定样本，交易成本和实施条件仍需要额外验证，研究者应当对参数选择和样本外表现进行充分解释，也不能将历史数据直接当作未来收益的可靠承诺，还必须保留完整的数据口径。") * 4
        m = material("00:00 甲：" + paragraph + "05:20 乙：" + paragraph + "12:30 甲：" + paragraph)
        self.assertTrue(cards.ready(m))
        self.assertTrue(kinds.classify(m)["eligible"])

    def test_plain_research_and_clock_times_are_not_publisher_blacklisted(self):
        for url in ("https://podcast.example.org/study", "https://research.example.org/study"):
            with self.subTest(url=url):
                m = material(PROSE * 6 + "The market opened at 09:30 and another auction occurred at 10:00. The final observation was at 16:00.", url)
                self.assertTrue(kinds.classify(m)["eligible"])

    def test_existing_material_failure_cannot_be_promoted(self):
        m = cards.make_material(title="Only a title", url="https://example.org/thin", source="synthetic", published="", paragraphs=[], evidence_level="title_only")
        self.assertEqual(kinds.classify(m)["kind"], "insufficient_material")
        self.assertFalse(kinds.classify(m)["eligible"])

    def test_new_shadow_run_does_not_spend_on_directory(self):
        m = material(TIMELINE)
        conf = {"run_label": "test", "endpoint": "https://example.org/chat", "generator": "gen", "reviewer": "review",
                "thinking": None, "max_requests": 4, "max_reserved_output_tokens": 11200,
                "generation_tokens": 2400, "review_tokens": 3200, "max_input_chars": 60000}
        def forbidden(*a, **k):raise AssertionError("directory reached API")
        with tempfile.TemporaryDirectory() as p:
            report = shadow.advance([m], conf, "source", Path(p), live=True, max_new_requests=4, api_key="unused", transport=forbidden)
            self.assertEqual(report["budget"]["requests_reserved"], 0)
            self.assertEqual(report["items"][0]["status"], "source_text_required")

    def test_selection_prefers_substantive_revision_for_same_url(self):
        directory = material(TIMELINE * 3)
        article = material(PROSE * 3)
        self.assertEqual(shadow.select_materials([directory, article], 1), [article])


if __name__ == "__main__":unittest.main()
