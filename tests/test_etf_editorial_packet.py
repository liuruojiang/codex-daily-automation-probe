import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import etf_content_cards as cards
import etf_semantic_review as semantic
import etf_review_appeal as appeals
import etf_editorial_packet as editorial

TEXT = ("The study reports a 12% historical return before transaction costs and uses a limited sample of portfolio observations. "
        "The authors explain that these observations cannot establish future performance and that implementation costs have not been tested.")


def fixture():
    material = cards.make_material(title="Portfolio study", url="https://example.org/study", source="synthetic", published="2026-03-18", paragraphs=[TEXT])
    card = cards.expand_evidence_ids(material, {"material_id": material["material_id"], "title_zh": "组合历史表现研究",
            "facts": [{"text_zh": "作者报告12%的历史收益，未扣交易成本。", "evidence_ids": ["p1.s1"]}], "limitations": [], "questions": ["样本外表现如何？"]})
    review = {"material_id": material["material_id"], "card_hash": cards.digest(card), "overall": "pass", "checks": [
        {"claim_id": cid, "verdict": "supported", "reason_zh": "该项与材料一致。", "evidence_ids": ["p1.s1"] if cid.startswith("facts.") else []} for cid in semantic.claim_map(card)]}
    record = {"material_id": material["material_id"], "card_hash": cards.digest(card), "review_hash": cards.digest(review), "appeal_case_id": None,
              "checks": [{"claim_id": cid, "decision": "confirmed", "reason_zh": "已按原文范围逐项核对。", "evidence_ids": ["p1.s1"] if cid.startswith("facts.") else []} for cid in semantic.claim_map(card)]}
    return material, card, review, record


class EditorialTests(unittest.TestCase):
    def test_complete_source_editing_only_enters_research_preview(self):
        m, c, r, e = fixture();row = editorial.evaluate(m, c, r, e)
        self.assertEqual(row["status"], "source_review_complete_for_research_preview")
        self.assertFalse(row["delivery_eligible"])
        self.assertEqual(row["preview_purpose"], "research_sample")
        self.assertEqual(row["external_fact_check"], "not_performed")

    def test_model_pass_still_needs_editor(self):
        m, c, r, e = fixture()
        self.assertEqual(editorial.evaluate(m, c, r, None)["status"], "awaiting_editorial_review")

    def test_missing_duplicate_or_foreign_editor_claim_fails(self):
        for mutation in (lambda e:e["checks"].pop(), lambda e:e["checks"].append(copy.deepcopy(e["checks"][0])),
                         lambda e:e["checks"][0].update(claim_id="other")):
            m, c, r, e = fixture();mutation(e)
            with self.assertRaises(ValueError):editorial.evaluate(m, c, r, e)

    def test_source_quote_must_be_checked_not_an_unrelated_sentence(self):
        m, c, r, e = fixture();e["checks"][1]["evidence_ids"] = ["p1.s2"]
        with self.assertRaisesRegex(ValueError, "own quotation"):editorial.evaluate(m, c, r, e)

    def test_stale_editor_record_does_not_apply_to_new_review(self):
        m, c, r, e = fixture();r["checks"][0]["reason_zh"] = "这是一份新复核说明。"
        with self.assertRaisesRegex(ValueError, "stale"):editorial.evaluate(m, c, r, e)

    def test_editor_cannot_override_model_or_number_rejection(self):
        m, c, r, e = fixture();r["overall"] = "revise";r["checks"][-1]["verdict"] = "needs_revision"
        e["review_hash"] = cards.digest(r)
        self.assertEqual(editorial.evaluate(m, c, r, e)["status"], "semantic_resolution_required")
        c["facts"][0]["text_zh"] = "作者报告99%的收益。"
        self.assertEqual(editorial.evaluate(m, c, r, e)["status"], "blocked_by_evidence")

    def test_editor_rejection_blocks_model_pass(self):
        m, c, r, e = fixture();e["checks"][-1]["decision"] = "needs_revision"
        self.assertEqual(editorial.evaluate(m, c, r, e)["status"], "editorial_revision_required")

    def test_exact_valid_appeal_and_full_editor_review_preserve_original_rejection(self):
        m, c, r, e = fixture();r["overall"] = "revise";r["checks"][-1]["verdict"] = "insufficient_evidence"
        petition = {"material_id": m["material_id"], "card_hash": cards.digest(c), "review_hash": cards.digest(r),
                    "objections": [{"claim_id": "questions.0", "reason_zh": "询问未披露的样本外结果属于合理追问。", "evidence_ids": ["p1.s2"]}]}
        case = appeals.make_case(m, c, r, petition, "original-reviewer")
        result = {"case_id": case["case_id"], "checks": [{"claim_id": "questions.0", "decision": "withdraw_objection", "reason_zh": "问题本身未假设未来结果，缺少答案不构成错误。", "evidence_ids": ["p1.s2"]}]}
        e.update(review_hash=cards.digest(r), appeal_case_id=case["case_id"])
        original = copy.deepcopy(r)
        row = editorial.evaluate(m, c, r, e, case, result)
        self.assertEqual(row["status"], "source_review_complete_for_research_preview")
        self.assertEqual(row["original_model_verdict"], "revise")
        self.assertEqual(r, original)
        result["checks"] = []
        self.assertEqual(editorial.evaluate(m, c, r, e, case, result)["status"], "semantic_resolution_required")

    def test_missing_or_stale_appeal_cannot_be_attached(self):
        m, c, r, e = fixture()
        with self.assertRaises(ValueError):editorial.evaluate(m, c, r, e, None, {"case_id": "other"})


if __name__ == "__main__":unittest.main()
