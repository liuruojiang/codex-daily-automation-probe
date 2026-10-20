import html
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import etf_content_cards as cards
from etf_transcript_material import extract

TEXT = ("The study reports a 12% historical return before transaction costs and uses a limited sample of portfolio observations. "
        "The authors explain that these observations cannot establish future performance and that implementation costs have not been tested.")


def fixture():
    parent = cards.make_material(title="Portfolio discussion", url="https://example.org/discussion", source="synthetic", published="2026-03-18T05:00:00Z", paragraphs=["Original directory."])
    page = ('<html><head><link rel="canonical" href="https://example.org/discussion"></head><body>'
            '<h1>Portfolio discussion</h1><div class="single-podcast-heading__content__desc__title-date">Episode 34 <span>18 | 03 | 2026</span></div>'
            '<div id="single-podcast-content-transcript-content">'
            '<h4>[00:00:00] Intro</h4><p>Promotional introduction.</p>'
            '<h4>[00:02:00] Analyst</h4><p>' + TEXT + '</p><p>Assets &amp; liabilities require attention.</p>'
            '<h4>[00:04:00] Host</h4><p>Another discussion outside the selected excerpt.</p>'
            '<h4>[00:06:00] Ending</h4><p>Promotional outro and disclaimers.</p></div>'
            '<p>Outside content must not enter the material.</p></body></html>')
    return parent, page.encode()


class TranscriptTests(unittest.TestCase):
    def test_selects_whole_speaker_turn_and_preserves_parent_date(self):
        parent, raw = fixture();new, provenance = extract(parent, raw, [["00:02:00", "00:02:00"]])
        self.assertEqual(new["published"], parent["published"])
        self.assertNotEqual(new["material_id"], parent["material_id"])
        self.assertEqual(new["evidence_level"], "captured_excerpt")
        self.assertFalse(provenance["complete_transcript_claim"])
        self.assertEqual(provenance["visible_episode_date"], "2026-03-18")
        self.assertEqual(len(new["paragraphs"]), 2)
        self.assertIn(TEXT, new["paragraphs"][0]["text"])
        self.assertTrue(new["paragraphs"][0]["text"].startswith("[00:02:00] Analyst:"))
        self.assertIn("Assets & liabilities", new["paragraphs"][1]["text"])
        self.assertNotIn("outside", str(new).lower())

    def test_intro_ending_and_surrounding_page_are_excluded(self):
        parent, raw = fixture();new, record = extract(parent, raw, [["00:00:00", "00:06:00"]])
        self.assertEqual([t["speaker"] for t in record["selected_turns"]], ["Analyst", "Host"])
        self.assertNotIn("Promotional", str(new))
        self.assertNotIn("Outside content", str(new))

    def test_mismatched_title_or_canonical_is_rejected(self):
        parent, raw = fixture()
        for replacement in (raw.replace(b"Portfolio discussion</h1>", b"Different episode</h1>"),
                            raw.replace(b'href="https://example.org/discussion"', b'href="https://example.org/other"')):
            with self.assertRaises(ValueError):extract(parent, replacement, [["00:02:00", "00:02:00"]])

    def test_missing_duplicate_or_unbalanced_container_is_rejected(self):
        parent, raw = fixture()
        for changed in (raw.replace(b'id="single-podcast-content-transcript-content"', b'id="other"'),
                        raw.replace(b'</body>', b'<div id="single-podcast-content-transcript-content"></div></body>'),
                        raw.replace(b'</body>', b'')):
            with self.assertRaises(ValueError):extract(parent, changed, [["00:02:00", "00:02:00"]])

    def test_empty_windows_or_window_without_content_is_rejected(self):
        parent, raw = fixture()
        for windows in ([], [["00:03:00", "00:02:00"]], [["00:00:00", "00:00:00"]], [["bad", "00:02:00"]]):
            with self.assertRaises(ValueError):extract(parent, raw, windows)

    def test_changed_raw_snapshot_changes_provenance_without_overwriting_parent(self):
        parent, raw = fixture();before = cards.digest(parent)
        one, a = extract(parent, raw, [["00:02:00", "00:02:00"]])
        two, b = extract(parent, raw.replace(b'<body>', b'<body><!-- revision -->'), [["00:02:00", "00:02:00"]])
        self.assertEqual(one, two)
        self.assertNotEqual(a["raw_html_sha256"], b["raw_html_sha256"])
        self.assertEqual(before, cards.digest(parent))


if __name__ == "__main__":unittest.main()
