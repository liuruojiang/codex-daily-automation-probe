import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"scripts"))
import etf_card_repair as repair
import etf_content_cards as cards
import etf_research_resolution as resolution
from test_etf_original_research import fixture as original_fixture, hold


def fixture():
    values = original_fixture();hold(values)
    materials, prior, source, registry = values;material = materials[0];mid = material["material_id"]
    old = prior["entries"][0];parent = cards.expand_evidence_ids(material, old["card"])
    facts = copy.deepcopy(old["card"]["facts"]);facts[0]["text_zh"] += "此处仅转述原文。"
    veto = {"material_id": mid, "card_hash": cards.digest(parent), "decision": "needs_revision", "reason_zh": "需收紧原文转述的范围。"}
    plan = {"material_id": mid, "parent_card_hash": cards.digest(parent), "parent_veto_hash": cards.digest(veto),
            "issues": [{"id": "scope", "reason_zh": veto["reason_zh"]}],
            "edits": [{"field": "facts", "value": facts, "issue_ids": ["scope"]}]}
    ledger = repair.revise(material, parent, plan, veto);current = ledger["card"]
    entry = copy.deepcopy(old);entry["card"]["facts"] = facts
    review = entry["review"];review.update(overall="pass", card_hash=cards.digest(current))
    for check in review["checks"]:check["verdict"] = "supported"
    entry["editor"].update(card_hash=cards.digest(current), review_hash=cards.digest(review))
    for check in entry["editor"]["checks"]:check["decision"] = "confirmed"
    disposition = {"revision_id": ledger["revision_id"], "card_hash": ledger["card_hash"],
                   "checks": [{"issue_id": "scope", "decision": "resolved", "reason_zh": "已补上原文转述的范围。"}]}
    record = {"version": resolution.VERSION, "prior_research_sha256": cards.digest(prior), "source_record_sha256": cards.digest(source),
              "registry_sha256": cards.digest(registry), "snapshot": registry["snapshot"], "delivery_eligible": False,
              "mail_approvals_added": 0, "external_fact_check": "not_performed", "entry": entry,
              "repair": {"parent_veto": veto, "plan": plan, "revision_id": ledger["revision_id"], "disposition": disposition},
              "issue_claims": [{"issue_id": "scope", "claim_ids": ["facts.0"], "action": "revise"}]}
    return material, record, prior, source, registry


class ResearchResolutionTests(unittest.TestCase):
    def test_changed_card_pass_does_not_rewrite_original_refusal_or_mail(self):
        values = fixture();before = copy.deepcopy(values)
        with patch.object(cards, "call_json", side_effect=AssertionError("no model")):
            first = resolution.build(*values);second = resolution.build(*values)
        self.assertEqual(values, before);self.assertEqual(first, second)
        self.assertEqual(first["original_model_overall"], "revise")
        self.assertEqual(first["current_model_overall"], "pass")
        self.assertEqual(first["original_refused_claims"], ["facts.0"])
        self.assertEqual(first["effective_original_research_complete"], 1)
        self.assertEqual(first["effective_original_research_held"], 0)
        self.assertFalse(first["delivery_eligible"]);self.assertEqual(first["mail_approvals_added"], 0)

    def test_old_record_source_or_registry_drift_is_rejected(self):
        for index in (2, 3, 4):
            values = fixture();values[index]["extra"] = "changed"
            with self.subTest(index=index), self.assertRaises(ValueError):resolution.build(*values)

    def test_original_hold_cannot_be_rewritten_as_complete(self):
        values = fixture();mid = values[0]["material_id"]
        values[2]["source_review_status"][mid] = "source_review_complete_for_research_preview"
        values[1]["prior_research_sha256"] = cards.digest(values[2])
        with self.assertRaisesRegex(ValueError, "held status"):resolution.build(*values)

    def test_changed_source_date_or_body_identity_is_rejected(self):
        for field in ("published_raw", "paragraph_text_sha256"):
            values = fixture();values[3]["original_sources"][0]["extraction"][field] = "changed"
            values[2]["parent_research_sha256"] = cards.digest(values[3])
            values[1].update(prior_research_sha256=cards.digest(values[2]), source_record_sha256=cards.digest(values[3]))
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "source identity"):resolution.build(*values)

    def test_every_original_rejection_needs_a_mapped_issue(self):
        for rows in ([], [{"issue_id": "scope", "claim_ids": ["questions.0"], "action": "revise"}],
                     [{"issue_id": "scope", "claim_ids": ["facts.0"], "action": "ignore"}]):
            values = fixture();values[1]["issue_claims"] = rows
            with self.subTest(rows=rows), self.assertRaises(ValueError):resolution.build(*values)

    def test_duplicate_mapping_cannot_fake_complete_issue_coverage(self):
        values = fixture();values[1]["issue_claims"] *= 2
        with self.assertRaisesRegex(ValueError, "mapping"):resolution.build(*values)

    def test_unchanged_refused_claim_cannot_be_resampled_through_another_edit(self):
        values = fixture();material, record, prior, _, _ = values;old = prior["entries"][0]
        parent = cards.expand_evidence_ids(material, old["card"]);veto = record["repair"]["parent_veto"]
        plan = copy.deepcopy(record["repair"]["plan"]);plan["edits"] = [{"field": "title_zh", "value": "调整后的研究标题", "issue_ids": ["scope"]}]
        ledger = repair.revise(material, parent, plan, veto)
        record["repair"].update(plan=plan, revision_id=ledger["revision_id"])
        record["entry"]["card"] = copy.deepcopy(old["card"]);record["entry"]["card"]["title_zh"] = "调整后的研究标题"
        with self.assertRaisesRegex(ValueError, "actually change"):resolution.build(*values)

    def test_original_review_cannot_approve_the_changed_card(self):
        values = fixture();values[1]["entry"]["review"] = copy.deepcopy(values[2]["entries"][0]["review"])
        with self.assertRaisesRegex(ValueError, "issue/model gates"):resolution.build(*values)

    def test_fresh_model_rejection_cannot_be_overridden_by_editor(self):
        values = fixture();review = values[1]["entry"]["review"]
        review["overall"] = "revise";review["checks"][1]["verdict"] = "needs_revision"
        values[1]["entry"]["editor"]["review_hash"] = cards.digest(review)
        with self.assertRaisesRegex(ValueError, "issue/model gates"):resolution.build(*values)

    def test_veto_or_unresolved_disposition_cannot_disappear(self):
        values = fixture();values[1]["repair"]["parent_veto"] = None
        with self.assertRaisesRegex(ValueError, "veto removed"):resolution.build(*values)
        values = fixture();values[1]["repair"]["disposition"]["checks"][0]["decision"] = "needs_revision"
        with self.assertRaisesRegex(ValueError, "issue/model gates"):resolution.build(*values)

    def test_stale_editor_cannot_approve_new_review(self):
        values = fixture();values[1]["entry"]["editor"]["review_hash"] = "stale"
        with self.assertRaisesRegex(ValueError, "editorial"):resolution.build(*values)

    def test_research_cannot_claim_delivery_or_external_verification(self):
        for update in ({"delivery_eligible": True}, {"mail_approvals_added": 1}, {"external_fact_check": "verified"}):
            values = fixture();values[1].update(update)
            with self.subTest(update=update), self.assertRaisesRegex(ValueError, "approve mail"):resolution.build(*values)


if __name__ == "__main__":unittest.main()
