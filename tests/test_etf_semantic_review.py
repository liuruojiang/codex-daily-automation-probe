from __future__ import annotations

import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import etf_content_cards as cards
import etf_semantic_review as sr

TEXT = (
    "The study reports a 12% historical return before transaction costs and uses a limited sample of portfolio observations. "
    "The authors explain that these observations cannot establish future performance and that implementation costs have not been tested."
)


def fixture():
    m = cards.make_material(title="Portfolio study", url="https://example.org/research", source="synthetic", published="", paragraphs=[TEXT])
    card = {"material_id": m["material_id"], "title_zh": "组合研究的样本与局限",
            "facts": [{"text_zh": "作者报告历史收益为12%，尚未扣除交易成本。",
                       "citations": [{"paragraph_id": "p1", "quote": TEXT.split('. ')[0] + '.'}]}],
            "limitations": [], "questions": ["样本外结果是否仍然成立？"]}
    review = {"material_id": m["material_id"], "card_hash": cards.digest(card), "overall": "pass",
              "checks": [{"claim_id": cid, "verdict": "supported", "reason_zh": "该陈述与所给材料范围一致。", "evidence_ids": ["p1.s1"] if cid.startswith("facts.") else []}
                         for cid in sr.claim_map(card)]}
    return m, card, review


class SemanticReviewTests(unittest.TestCase):
    def test_editorial_veto_cannot_force_a_pass_or_apply_to_changed_card(self):
        m, card, review = fixture()
        row = sr.evaluate(m, card, review)
        veto = {"material_id": m["material_id"], "card_hash": cards.digest(card), "decision": "needs_revision", "reason_zh": "该条引用未支持后半句，应补充依据。"}
        self.assertEqual(sr.editorial_veto(row, card, veto)["status"], "editorial_revision_required")
        veto["decision"] = "pass"
        with self.assertRaises(ValueError):sr.editorial_veto(row, card, veto)
        veto["decision"] = "needs_revision"
        card["questions"][0] = "新的问题是什么？"
        with self.assertRaises(ValueError):sr.editorial_veto(row, card, veto)
    def test_model_pass_never_becomes_external_fact_verification(self):
        m, card, review = fixture()
        self.assertEqual(sr.validate_review(m, card, review), [])
        row = sr.evaluate(m, card, review)
        self.assertEqual(row["status"], "model_review_passed")
        self.assertNotIn("verified", row)

    def test_one_failed_claim_overrides_overall_pass(self):
        m, card, review = fixture()
        review["checks"][1]["verdict"] = "contradicted"
        self.assertIn("overall_disagrees_with_checks", sr.validate_review(m, card, review))
        review["overall"] = "revise"
        self.assertEqual(sr.evaluate(m, card, review)["status"], "semantic_revision_required")

    def test_stale_card_review_is_rejected_even_if_only_a_question_changed(self):
        m, card, review = fixture()
        card["questions"][0] = "成本是否经独立验证？"
        self.assertIn("stale_review", sr.validate_review(m, card, review))

    def test_every_claim_must_be_present_exactly_once(self):
        m, card, review = fixture()
        for mutate in (lambda r: r["checks"].pop(), lambda r: r["checks"].append(copy.deepcopy(r["checks"][0])),
                       lambda r: r["checks"][0].update(claim_id="facts.99")):
            r = copy.deepcopy(review)
            mutate(r)
            self.assertTrue(sr.validate_review(m, card, r))

    def test_unknown_evidence_and_missing_supported_evidence_are_rejected(self):
        m, card, review = fixture()
        fact = next(c for c in review["checks"] if c["claim_id"] == "facts.0")
        fact["evidence_ids"] = ["p1.s999"]
        self.assertIn("invalid_review_evidence", sr.validate_review(m, card, review))
        fact["evidence_ids"] = []
        self.assertIn("supported_claim_has_no_evidence", sr.validate_review(m, card, review))

    def test_malformed_review_is_not_accepted_or_crashed(self):
        m, card, review = fixture()
        for value in (None, [], "pass"):
            self.assertEqual(sr.validate_review(m, card, value), ["invalid_review_schema"])
        for mutate in (lambda r: r.update(checks=None), lambda r: r.update(overall=[]),
                       lambda r: r["checks"][0].update(claim_id=[]), lambda r: r["checks"][0].update(evidence_ids=[[]]),
                       lambda r: r["checks"][0].update(verdict={}), lambda r: r["checks"][0].update(reason_zh=None)):
            r = copy.deepcopy(review)
            mutate(r)
            self.assertTrue(sr.validate_review(m, card, r))

    def test_mechanical_failure_blocks_paid_review(self):
        m, card, review = fixture()
        card["facts"][0]["text_zh"] = "作者报告收益为99%。"
        self.assertEqual(sr.evaluate(m, card, review)["status"], "blocked_by_evidence")
        with self.assertRaisesRegex(ValueError, "mechanical"):
            sr.review_payload(m, card, "model")

    def test_review_payload_includes_original_material_and_all_claims(self):
        m, card, review = fixture()
        payload = sr.review_payload(m, card, "review-model")
        data = json.loads(payload["messages"][1]["content"])
        self.assertEqual(data["card_hash"], cards.digest(card))
        self.assertEqual(set(data["claims"]), {"title", "facts.0", "questions.0"})
        self.assertIn("p1.s1", data["evidence_units"])
        self.assertEqual(data["required_claim_ids"][0], "title")
        self.assertNotIn("thinking", payload)

    def test_bilingual_guards_apply_to_claim_citations_not_other_paragraphs(self):
        m, card, review = fixture()
        card["facts"][0] = {"text_zh": "研究季度小时合约。", "citations": [{"paragraph_id": "p1", "quote": "between Quarter-Hourly and Hourly contracts"}]}
        self.assertEqual(sr.terminology_issues(card)[0]["rule"], "quarter_hour_is_not_quarter_year")
        card["facts"][0] = {"text_zh": "作者使用2024年每季度第一个月的数据。", "citations": [{"paragraph_id": "p1", "quote": "the first month of each quarter of 2024"}]}
        self.assertEqual(sr.terminology_issues(card), [])
        card["facts"][0] = {"text_zh": "作者称年化投资收益率为5.05%。", "citations": [{"paragraph_id": "p1", "quote": "a 5.05% guaranteed withdrawal rate over 30 years"}]}
        self.assertEqual(sr.terminology_issues(card)[0]["rule"], "withdrawal_is_not_investment_return")

    def test_html_escapes_reviewer_text(self):
        m, card, review = fixture()
        review["checks"][0]["reason_zh"] = "<script>恶意内容</script>"
        text = sr.render_html({"items": [sr.evaluate(m, card, review)]})
        self.assertNotIn("<script>", text)

    def test_cli_resumes_and_configuration_change_cannot_reuse_review(self):
        m, card, review = fixture()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cards.write_json(root / "materials.json", [m])
            cards.write_json(root / "responses.json", {m["material_id"]: card})
            args = ["review", "--materials", str(root / "materials.json"), "--responses", str(root / "responses.json"), "--out-dir", str(root / "out")]
            config = {"ETF_REVIEW_API_URL": "https://example.org/chat", "ETF_REVIEW_MODEL": "review-model", "ETF_REVIEW_API_KEY": "secret"}
            raw = {"model": "review-model", "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(review)}}]}
            with patch.dict(cards.os.environ, config), patch.object(cards, "call_json", return_value=raw) as call:
                self.assertEqual(sr.main(args), 0)
                self.assertEqual(sr.main(args), 0)
                self.assertEqual(call.call_count, 1)
                with patch.dict(cards.os.environ, {"ETF_REVIEW_MODEL": "different"}), patch("sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit):
                    sr.main(args)
                self.assertEqual(call.call_count, 1)

    def test_invalid_model_review_is_visible_and_not_blindly_retried(self):
        m, card, review = fixture()
        review["checks"] = []
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cards.write_json(root / "materials.json", [m])
            cards.write_json(root / "responses.json", {m["material_id"]: card})
            args = ["review", "--materials", str(root / "materials.json"), "--responses", str(root / "responses.json"), "--out-dir", str(root / "out")]
            config = {"ETF_REVIEW_API_URL": "https://example.org/chat", "ETF_REVIEW_MODEL": "review-model", "ETF_REVIEW_API_KEY": "secret"}
            raw = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(review)}}]}
            with patch.dict(cards.os.environ, config), patch.object(cards, "call_json", return_value=raw) as call:
                self.assertEqual(sr.main(args), 1)
                row = cards.read_json(root / "out/semantic-report.json")["items"][0]
                self.assertEqual(row["status"], "review_failed")
                self.assertEqual(sr.main(args), 1)
                self.assertEqual(call.call_count, 1)


if __name__ == "__main__":
    unittest.main()
