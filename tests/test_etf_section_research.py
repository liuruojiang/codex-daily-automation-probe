import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"scripts"))
import etf_content_cards as cards
import etf_card_repair as repair
import etf_original_sources as original
import etf_section_research as research
import etf_semantic_review as semantic

TEXT = "Daily prices produce signals, whereas bid and ask quotes estimate spreads; this distinction matters for research and execution."


def fixture():
    url = "https://example.org/futures"
    raw = ('<html><head><link rel="canonical" href="'+url+'"><meta property="article:published_time" content="2026-10-16T09:16:44+00:00"></head>'
           '<body><h1>Futures security master</h1><article><h2>Introduction</h2><p>The opening context is intentionally outside the selected chapters.</p>'
           '<h2>Data sources</h2><p>'+TEXT+'</p><h3>Signal data</h3><p>Paid data also needs quality checks; historical gaps must be explicitly recorded and investigated before use.</p>'
           '<h2>Process automation</h2><p>Reports identify candidates, but matching still requires human review of identifiers, units and settlement terms.</p>'
           '<h3>Rebuild and check</h3><p>Failed checks return the source definitions for investigation. The revised master must produce consistent histories before recalibration.</p>'
           '<h2>Conclusion</h2><p>Conclusion remains outside this explicit two-section selection.</p></article></body></html>').encode()
    lead = {"url": url, "parent_material_id": "directory"}
    spec = {"title_selector": "h1", "expected_title": "Futures security master", "body_selector": "article"}
    parent, extraction = original.extract(lead, raw, spec)
    registry = {"version": "etf-reviewed-preview-registry-v1", "snapshot": {}, "entries": []}
    source = {"original_sources": [{"extraction": extraction}], "registry_sha256": cards.digest(registry), "snapshot": {}}
    parent, material, selection = research.extract_sections(source, parent["material_id"], raw, ["Data sources", "Process automation"])
    mid = material["material_id"]
    compact = {"material_id": mid, "title_zh": "期货主数据的两个章节选读", "facts": [
        {"text_zh": "作者将日线用于信号，买卖报价用于价差估计。", "evidence_ids": ["p2.s1"]}], "limitations": [], "questions": ["后续能否复现这些检查？"]}
    card = cards.expand_evidence_ids(material, compact)
    review = {"material_id": mid, "card_hash": cards.digest(card), "overall": "pass", "checks": [
        {"claim_id": cid, "verdict": "supported", "reason_zh": "该项与选段相符。", "evidence_ids": ["p2.s1"] if cid.startswith("facts.") else []} for cid in semantic.claim_map(card)]}
    editor = {"material_id": mid, "card_hash": cards.digest(card), "review_hash": cards.digest(review), "appeal_case_id": None, "checks": [
        {"claim_id": cid, "decision": "confirmed", "reason_zh": "已按同一原句核对。", "evidence_ids": ["p2.s1"] if cid.startswith("facts.") else []} for cid in semantic.claim_map(card)]}
    prior = {"registry_sha256": cards.digest(registry), "snapshot": {}}
    record = {"version": research.VERSION, "source_record_sha256": cards.digest(source), "prior_resolution_sha256": cards.digest(prior),
              "registry_sha256": cards.digest(registry), "snapshot": {}, "delivery_eligible": False, "mail_approvals_added": 0,
              "external_fact_check": "not_performed", "selection": selection, "repair": None, "initial_card_sha256": cards.digest(card),
              "entry": {"material_id": mid, "url": url, "card": compact, "review": review, "editor": editor, "annotation": {}}}
    return (parent, material, record, source, prior, registry), raw


class SectionResearchTests(unittest.TestCase):
    def test_whole_sections_keep_subheadings_and_original_identity(self):
        values, _ = fixture();parent, material, record, *_ = values;selection = record["selection"]
        self.assertEqual(selection["selected_paragraph_count"], 8)
        self.assertEqual([s["paragraph_count"] for s in selection["sections"]], [4, 4])
        self.assertIn("Signal data", [p["text"] for p in material["paragraphs"]])
        self.assertNotIn("Conclusion", [p["text"] for p in material["paragraphs"]])
        self.assertEqual(material["published"], parent["published"])
        self.assertEqual(material["title"], parent["title"])
        self.assertNotEqual(material["material_id"], parent["material_id"])
        self.assertEqual(selection["paragraph_map"][0], {"id": "p1", "parent_id": "p3"})

    def test_replay_does_not_call_models_or_change_old_context(self):
        values, _ = fixture();before = copy.deepcopy(values)
        with patch.object(cards, "call_json", side_effect=AssertionError("no model")):
            first = research.build(*values);second = research.build(*values)
        self.assertEqual(first, second);self.assertEqual(values, before)
        self.assertFalse(first["delivery_eligible"]);self.assertFalse(first["automatic_truncation"])

    def test_unknown_duplicate_or_reordered_sections_are_rejected(self):
        values, _ = fixture();parent, _, record, *_ = values
        for titles in (["Missing"], ["Data sources", "Data sources"], ["Process automation", "Data sources"]):
            with self.subTest(titles=titles), self.assertRaises(ValueError):research.select_sections(parent, record["selection"]["headings"], titles)

    def test_heading_offsets_cannot_point_inside_a_paragraph_or_reorder(self):
        values, _ = fixture();parent, _, record, *_ = values
        for change in (lambda h:h[1].update(parent_id="p4"), lambda h:h.reverse()):
            headings = copy.deepcopy(record["selection"]["headings"]);change(headings)
            with self.subTest(), self.assertRaises(ValueError):research.select_sections(parent, headings, ["Data sources"])

    def test_partial_section_or_full_article_claim_cannot_be_forged(self):
        for mutate in (lambda s:s["paragraph_map"].pop(), lambda s:s["sections"][0].update(end_parent_id="p4"),
                       lambda s:s.update(complete_article_claim=True), lambda s:s.update(automatic_truncation=True)):
            values, _ = fixture();mutate(values[2]["selection"])
            with self.subTest(), self.assertRaisesRegex(ValueError, "boundaries/material"):research.build(*values)

    def test_changed_source_body_cannot_silently_refresh_selection(self):
        values, raw = fixture();source = values[3];mid = values[0]["material_id"]
        with self.assertRaisesRegex(ValueError, "parent body/date changed"):
            research.extract_sections(source, mid, raw.replace(b"Daily prices", b"Changed prices"), ["Data sources"])

    def test_naive_date_or_subscription_parent_cannot_be_selected(self):
        for update in ({"date_status": "timezone_unconfirmed"}, {"access_scope": "metadata_only_subscription_marker"}):
            values, raw = fixture();values[3]["original_sources"][0]["extraction"].update(update)
            with self.subTest(update=update), self.assertRaisesRegex(ValueError, "date/access held"):
                research.extract_sections(values[3], values[0]["material_id"], raw, ["Data sources"])

    def test_oversized_whole_section_fails_instead_of_truncating(self):
        long_text = "The definitions require independent checks of contract identities, units and settlement assumptions before any recalibration. "+(TEXT+" ")*600
        parent = cards.make_material(title="Long source", url="https://example.org/long", source="example.org", published="2026-10-16T09:16:44Z",
                                     paragraphs=["Long section", long_text, "Next section", "End."])
        with self.assertRaisesRegex(ValueError, "still exceeds input"):
            research.select_sections(parent, [{"title": "Long section", "parent_id": "p1"}, {"title": "Next section", "parent_id": "p3"}], ["Long section"])
        self.assertEqual(parent["paragraphs"][1]["text"], long_text)

    def test_prior_research_or_registry_context_must_be_unchanged(self):
        for index in (3, 4, 5):
            values, _ = fixture();values[index]["extra"] = "changed"
            with self.subTest(index=index), self.assertRaises(ValueError):research.build(*values)

    def test_changed_card_needs_repair_history_and_new_review(self):
        values, _ = fixture();values[2]["entry"]["card"]["title_zh"] = "被修改的新标题"
        with self.assertRaisesRegex(ValueError, "lost repair history"):research.build(*values)

    def test_repair_preserves_initial_model_pass_and_editor_veto(self):
        values, _ = fixture();material, record = values[1], values[2];entry = record["entry"]
        old_compact = copy.deepcopy(entry["card"]);parent = cards.expand_evidence_ids(material, old_compact)
        old_review = copy.deepcopy(entry["review"]);mid = material["material_id"]
        veto = {"material_id": mid, "card_hash": cards.digest(parent), "decision": "needs_revision", "reason_zh": "标题应更明确表明章节范围。"}
        plan = {"material_id": mid, "parent_card_hash": cards.digest(parent), "parent_veto_hash": cards.digest(veto),
                "issues": [{"id": "scope", "reason_zh": veto["reason_zh"]}],
                "edits": [{"field": "title_zh", "value": "数据来源与自动化章节选读", "issue_ids": ["scope"]}]}
        ledger = repair.revise(material, parent, plan, veto)
        entry["card"]["title_zh"] = ledger["card"]["title_zh"];entry["review"]["card_hash"] = ledger["card_hash"]
        entry["editor"].update(card_hash=ledger["card_hash"], review_hash=cards.digest(entry["review"]))
        record["repair"] = {"parent_card": old_compact, "parent_review": old_review, "parent_veto": veto,
                            "plan": plan, "revision_id": ledger["revision_id"], "disposition": {
                                "revision_id": ledger["revision_id"], "card_hash": ledger["card_hash"],
                                "checks": [{"issue_id": "scope", "decision": "resolved", "reason_zh": "已标明选段范围。"}]}}
        self.assertEqual(research.build(*values)["status"], "source_review_complete_for_research_preview")
        for mutate in (lambda r:r.update(parent_veto=None), lambda r:r["parent_review"].update(card_hash="stale"),
                       lambda r:r["disposition"]["checks"][0].update(decision="needs_revision")):
            changed = copy.deepcopy(values);mutate(changed[2]["repair"])
            with self.subTest(), self.assertRaises(ValueError):research.build(*changed)

    def test_model_rejection_or_stale_editor_cannot_be_overridden(self):
        values, _ = fixture();review = values[2]["entry"]["review"]
        review["overall"] = "revise";review["checks"][-1]["verdict"] = "needs_revision"
        values[2]["entry"]["editor"]["review_hash"] = cards.digest(review)
        with self.assertRaisesRegex(ValueError, "source editing incomplete"):research.build(*values)
        values, _ = fixture();values[2]["entry"]["editor"]["card_hash"] = "stale"
        with self.assertRaisesRegex(ValueError, "editorial"):research.build(*values)

    def test_research_cannot_approve_mail_or_external_facts(self):
        for update in ({"delivery_eligible": True}, {"mail_approvals_added": 1}, {"external_fact_check": "verified"}):
            values, _ = fixture();values[2].update(update)
            with self.subTest(update=update), self.assertRaisesRegex(ValueError, "approve mail"):research.build(*values)


if __name__ == "__main__":unittest.main()
