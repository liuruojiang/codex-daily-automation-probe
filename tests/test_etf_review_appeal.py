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
import etf_semantic_review as semantic
import etf_review_appeal as appeal

TEXT = ("The study reports a 12% historical return before transaction costs and uses a limited sample of portfolio observations. "
        "The authors explain that these observations cannot establish future performance and that implementation costs have not been tested.")


def fixture():
    m = cards.make_material(title="Portfolio study", url="https://example.org/study", source="synthetic", published="", paragraphs=[TEXT])
    card = cards.expand_evidence_ids(m, {"material_id": m["material_id"], "title_zh": "组合研究的表现",
            "facts": [{"text_zh": "作者报告历史收益为12%，尚未扣除交易成本。", "evidence_ids": ["p1.s1"]}],
            "limitations": [], "questions": ["样本外表现如何？"]})
    review = {"material_id": m["material_id"], "card_hash": cards.digest(card), "overall": "revise",
              "checks": [{"claim_id": cid, "verdict": "insufficient_evidence" if cid.startswith("questions.") else "supported",
                          "reason_zh": "原文没有回答问题。" if cid.startswith("questions.") else "陈述与材料相符。",
                          "evidence_ids": ["p1.s1"] if cid.startswith("facts.") else []} for cid in semantic.claim_map(card)]}
    petition = {"material_id": m["material_id"], "card_hash": cards.digest(card), "review_hash": cards.digest(review),
                "objections": [{"claim_id": "questions.0", "reason_zh": "这是询问未知结果，没有声称原文给出了样本外表现。", "evidence_ids": ["p1.s2"]}]}
    return m, card, review, petition


def case_fixture():
    m, card, review, petition = fixture()
    return m, appeal.make_case(m, card, review, petition, "original-reviewer")


def withdrawn(case):
    return {"case_id": case["case_id"], "checks": [{"claim_id": o["claim_id"], "decision": "withdraw_objection",
            "reason_zh": "问题合理询问未披露内容，缺少答案本身不能构成拒绝理由。", "evidence_ids": ["p1.s2"]} for o in case["petition"]["objections"]]}


def transport(calls):
    def send(request, timeout):
        calls.append(1)
        packet = json.loads(json.loads(request.data)["messages"][1]["content"])
        return io.BytesIO(json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(withdrawn(packet))}}],
                                     "usage": {"total_tokens": 100}}).encode())
    return send


class AppealTests(unittest.TestCase):
    def execute(self, m, case, p, calls, mode="run", **options):
        return appeal.execute(m, case, Path(p), model="appeal-reviewer", endpoint="https://example.org/chat", mode=mode,
                              api_key="synthetic-secret", transport=transport(calls), **options)

    def test_original_review_and_card_are_preserved_not_rewritten(self):
        m, card, review, petition = fixture();before = copy.deepcopy((card, review, petition))
        case = appeal.make_case(m, card, review, petition, "original-reviewer")
        row = appeal.evaluate(m, case, withdrawn(case))
        self.assertEqual((card, review, petition), before)
        self.assertEqual(row["original_review"], review)
        self.assertEqual(row["original_status"], "semantic_revision_required")
        self.assertEqual(row["status"], "appeal_supports_editorial_recheck")
        self.assertEqual(row["editorial_review"], "required")
        self.assertEqual(row["external_fact_check"], "not_performed")

    def test_changed_card_or_review_invalidates_petition(self):
        m, card, review, petition = fixture()
        card["questions"][0] = "另一个问题是什么？"
        with self.assertRaises(ValueError):appeal.make_case(m, card, review, petition, "original-reviewer")
        m, card, review, petition = fixture();review["checks"][-1]["reason_zh"] = "原判理由已经改变。"
        with self.assertRaisesRegex(ValueError, "stale"):appeal.make_case(m, card, review, petition, "original-reviewer")

    def test_supported_claims_cannot_be_used_to_clear_other_rejections(self):
        m, card, review, petition = fixture();petition["objections"][0]["claim_id"] = "facts.0"
        with self.assertRaises(ValueError):appeal.make_case(m, card, review, petition, "original-reviewer")

    def test_incomplete_original_review_cannot_be_appealed(self):
        m, card, review, petition = fixture();review["checks"].pop(0)
        petition["review_hash"] = cards.digest(review)
        with self.assertRaisesRegex(ValueError, "complete original review"):
            appeal.make_case(m, card, review, petition, "original-reviewer")

    def test_source_text_gate_cannot_be_overruled_by_appeal(self):
        m, card, review, petition = fixture()
        m = cards.make_material(title=m["title"], url=m["url"], source=m["source"], published="", paragraphs=[TEXT + " 00:00 - Introduction 05:20 - Risk and return 12:30 - Discussion and conclusions"])
        with self.assertRaisesRegex(ValueError, "source text gate"):
            appeal.make_case(m, card, review, petition, "original-reviewer")

    def test_invalid_numbers_cannot_be_cleared_by_appeal(self):
        m, card, review, petition = fixture();card["facts"][0]["text_zh"] = "作者报告收益为99%。"
        with self.assertRaises(ValueError):appeal.make_case(m, card, review, petition, "original-reviewer")

    def test_petition_requires_valid_evidence_no_duplicate_claims(self):
        for mutate in (lambda p:p["objections"][0].update(evidence_ids=["p9.s9"]),
                       lambda p:p["objections"].append(copy.deepcopy(p["objections"][0]))):
            m, card, review, petition = fixture();mutate(petition)
            with self.assertRaises(ValueError):appeal.make_case(m, card, review, petition, "original-reviewer")

    def test_incomplete_or_stale_results_cannot_pass(self):
        m, case = case_fixture()
        for mutate in (lambda r:r.update(case_id="other"), lambda r:r.update(checks=[]),
                       lambda r:r["checks"].append(copy.deepcopy(r["checks"][0])), lambda r:r.update(overall="pass")):
            result = withdrawn(case);mutate(result)
            self.assertEqual(appeal.evaluate(m, case, result)["status"], "appeal_invalid")

    def test_withdrawal_needs_evidence_and_never_means_publication(self):
        m, case = case_fixture();result = withdrawn(case)
        result["checks"][0]["evidence_ids"] = []
        self.assertIn("invalid_appeal_evidence", appeal.validate_result(m, case, result))

    def test_unchallenged_rejection_remains_even_if_all_petition_items_withdrawn(self):
        m, card, review, petition = fixture();review["checks"][1]["verdict"] = "needs_revision"
        petition["review_hash"] = cards.digest(review)
        case = appeal.make_case(m, card, review, petition, "original-reviewer")
        row = appeal.evaluate(m, case, withdrawn(case))
        self.assertEqual(row["status"], "appeal_objections_remaining")
        self.assertEqual(row["outstanding_claims"], ["facts.0"])

    def test_uphold_or_inconclusive_preserves_original_objection(self):
        m, case = case_fixture()
        for decision in ("uphold", "inconclusive"):
            result = withdrawn(case);result["checks"][0]["decision"] = decision
            self.assertEqual(appeal.evaluate(m, case, result)["outstanding_claims"], ["questions.0"])

    def test_same_model_or_tampered_case_rejected_before_request(self):
        m, case = case_fixture()
        with self.assertRaises(ValueError):appeal.payload_for(m, case, "original-reviewer", 2400)
        case["original_review"]["checks"][-1]["reason_zh"] = "篡改后的理由。"
        with self.assertRaises(ValueError):appeal.payload_for(m, case, "appeal-reviewer", 2400)

    def test_one_request_per_case_including_repeated_run_and_replay(self):
        m, case = case_fixture();calls = []
        with tempfile.TemporaryDirectory() as p:
            for mode in ("run", "run", "replay"):
                row = self.execute(m, case, p, calls, mode)
                self.assertEqual(row["execution"]["requests_reserved"], 1)
            self.assertEqual(calls, [1])
            for f in Path(p).rglob("*.json"):self.assertNotIn("synthetic-secret", f.read_text(encoding="utf-8"))

    def test_completed_receipt_recovers_interruption_without_new_api_request(self):
        m, case = case_fixture();calls = []
        with tempfile.TemporaryDirectory() as p:
            with patch.object(cards, "model_card", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):self.execute(m, case, p, calls)
            row = self.execute(m, case, p, calls, "replay")
            self.assertEqual(calls, [1])
            self.assertEqual(row["status"], "appeal_supports_editorial_recheck")

    def test_missing_receipt_after_reservation_is_unknown_not_retried(self):
        m, case = case_fixture();calls = []
        with tempfile.TemporaryDirectory() as p:
            with patch.object(cards, "call_json", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):self.execute(m, case, p, calls)
            row = self.execute(m, case, p, calls)
            self.assertEqual(calls, [])
            self.assertEqual(row["status"], "appeal_failed")
            self.assertEqual(row["execution"]["requests_reserved"], 1)

    def test_oversize_case_is_not_clipped_or_requested(self):
        m, case = case_fixture();calls = []
        with tempfile.TemporaryDirectory() as p:
            row = self.execute(m, case, p, calls, max_input_chars=1)
            self.assertEqual(calls, [])
            self.assertEqual(row["execution"]["requests_reserved"], 0)
            self.assertEqual(row["errors"], ["InputLimitExceeded"])

    def test_changed_request_limits_do_not_reuse_paid_case(self):
        m, case = case_fixture();calls = []
        with tempfile.TemporaryDirectory() as p:
            self.execute(m, case, p, calls)
            with self.assertRaisesRegex(ValueError, "context changed"):
                self.execute(m, case, p, calls, max_tokens=3200)
            self.assertEqual(calls, [1])


if __name__ == "__main__":unittest.main()
