import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"scripts"))
import etf_content_cards as cards
import etf_original_sources as original

TEXT = ("This is a synthetic original research paragraph describing data construction and separate validation targets. "
        "The author distinguishes observed outcomes from model assumptions and describes implementation limits. ")


def fixture(date="2026-10-18T10:00:00+00:00"):
    lead = {"url": "https://example.org/research", "title": "Directory alias", "parent_material_id": "directory",
            "publication_date": None, "date_status": "unverified", "delivery_eligible": False}
    spec = {"title_selector": "h1", "expected_title": "Original research title", "body_selector": "div.article-body"}
    page = ('<html><head><link rel="canonical" href="https://example.org/research">'
            '<meta property="article:published_time" content="'+date+'"></head><body><h1>Original research title</h1>'
            '<div class="article-body"><p>'+TEXT*3+'</p><p>Ending research paragraph with an explicit scope limitation.</p></div></body></html>')
    return lead, page.encode(), spec


class OriginalSourceTests(unittest.TestCase):
    def test_original_identity_and_date_are_independent_from_directory(self):
        lead, raw, spec = fixture();before = copy.deepcopy(lead)
        with patch.object(cards, "call_json", side_effect=AssertionError("no model")):
            material, record = original.extract(lead, raw, spec)
        self.assertEqual(lead, before)
        self.assertEqual(material["title"], "Original research title")
        self.assertEqual(material["source"], "example.org")
        self.assertEqual(material["published"], "2026-10-18T10:00:00+00:00")
        self.assertFalse(record["delivery_eligible"])
        self.assertFalse(record["complete_article_claim"])

    def test_naive_original_time_is_not_assigned_a_time_zone(self):
        lead, raw, spec = fixture("2026-10-18T10:00:00")
        material, record = original.extract(lead, raw, spec)
        self.assertEqual(material["published"], "2026-10-18T10:00:00")
        self.assertEqual(record["date_status"], "timezone_unconfirmed")
        self.assertIn("confirm_source_time_zone", record["pending_flags"])

    def test_conflicting_publication_metadata_is_refused(self):
        lead, raw, spec = fixture()
        raw = raw.replace(b'</head>', b'<meta property="article:published_time" content="2025-01-01T00:00:00Z"></head>')
        with self.assertRaisesRegex(ValueError, "ambiguous.*date"):original.extract(lead, raw, spec)

    def test_wrong_title_or_canonical_cannot_be_saved_as_original(self):
        lead, raw, spec = fixture()
        for value in (raw.replace(b'Original research title', b'Other title'), raw.replace(b'href="https://example.org/research"', b'href="https://example.org/other"')):
            with self.subTest(), self.assertRaises(ValueError):original.extract(lead, value, spec)

    def test_subscription_marker_stops_at_metadata_without_extracting_body(self):
        lead, raw, spec = fixture();raw = raw.replace(b'</head>', b'<script>{"audience":"only_paid"}</script></head>')
        material, record = original.extract(lead, raw, spec)
        self.assertIsNone(material)
        self.assertIsNone(record["material_id"])
        self.assertEqual(record["paragraph_count"], 0)
        self.assertEqual(record["access_scope"], "metadata_only_subscription_marker")

    def test_hidden_scripts_and_navigation_are_not_model_evidence(self):
        lead, raw, spec = fixture()
        raw = raw.replace(b'<div class="article-body">', b'<div class="article-body"><p hidden>HIDDEN_TEXT</p><script>SCRIPT_TEXT</script><nav>NAV_TEXT</nav><p style="display:none">STYLE_HIDDEN</p><blockquote><p>Nested public evidence remains once.</p></blockquote>')
        material, _ = original.extract(lead, raw, spec);text = " ".join(p["text"] for p in material["paragraphs"])
        for bad in ("HIDDEN_TEXT", "SCRIPT_TEXT", "NAV_TEXT", "STYLE_HIDDEN"):self.assertNotIn(bad, text)
        self.assertEqual(text.count("Nested public evidence"), 1)

    def test_missing_or_multiple_body_roots_are_not_guessed(self):
        lead, raw, spec = fixture()
        for value in (raw.replace(b'class="article-body"', b'class="other"'), raw.replace(b'</body>', b'<div class="article-body"><p>Other</p></div></body>')):
            with self.subTest(), self.assertRaisesRegex(ValueError, "body"):original.extract(lead, value, spec)

    def test_oversize_model_input_is_flagged_without_clipping_source(self):
        lead, raw, spec = fixture()
        raw = raw.replace(b'<p>'+TEXT.encode()*3+b'</p>', ('<p>'+TEXT*400+'END_MARKER</p>').encode())
        material, record = original.extract(lead, raw, spec)
        self.assertIn("END_MARKER", material["paragraphs"][0]["text"])
        self.assertGreater(record["generation_payload_chars"], 60000)
        self.assertIn("explicit_excerpt_selection_required", record["pending_flags"])

    def test_unsupported_math_does_not_become_garbled_text(self):
        lead, raw, spec = fixture();raw = raw.replace(b'</p>', b'<math><mi>x</mi></math></p>', 1)
        with self.assertRaisesRegex(ValueError, "mathematical-source"):original.extract(lead, raw, spec)

    def test_missing_date_does_not_inherit_directory_date(self):
        lead, raw, spec = fixture();raw = raw.replace(b'<meta property="article:published_time" content="2026-10-18T10:00:00+00:00">', b'')
        with self.assertRaisesRegex(ValueError, "date"):original.extract(lead, raw, spec)


if __name__ == "__main__":unittest.main()
