from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import etf_content_cards as cards
import etf_card_repair as repair
import etf_semantic_review as semantic

TEXT = ("The study reports a 12% historical return before transaction costs and uses a limited sample of portfolio observations. "
        "The authors explain that these observations cannot establish future performance and that implementation costs have not been tested.")


def fixture():
    material = cards.make_material(title="Portfolio study", url="https://example.org/research", source="synthetic", published="", paragraphs=[TEXT])
    card = {"material_id": material["material_id"], "title_zh": "组合研究的历史表现",
            "facts": [{"text_zh": "作者报告历史收益为12%。", "evidence_ids": ["p1.s1"]}],
            "limitations": [], "questions": []}
    card = cards.expand_evidence_ids(material, card)
    veto = {"material_id": material["material_id"], "card_hash": cards.digest(card), "decision": "needs_revision", "reason_zh": "收益描述应说明未扣除交易成本。"}
    plan = {"material_id": material["material_id"], "parent_card_hash": cards.digest(card), "parent_veto_hash": cards.digest(veto),
            "issues": [{"id": "cost", "reason_zh": veto["reason_zh"]}],
            "edits": [{"field": "facts", "value": [{"text_zh": "作者报告历史收益为12%，未扣除交易成本。", "evidence_ids": ["p1.s1"]}], "issue_ids": ["cost"]}]}
    return material, card, veto, plan


def review_for(material, card):
    return {"material_id": material["material_id"], "card_hash": cards.digest(card), "overall": "pass",
            "checks": [{"claim_id": cid, "verdict": "supported", "reason_zh": "该项与原文一致。", "evidence_ids": ["p1.s1"] if cid.startswith("facts.") else []} for cid in semantic.claim_map(card)]}


def resolved(record):
    return {"revision_id": record["revision_id"], "card_hash": record["card_hash"],
            "checks": [{"issue_id": "cost", "decision": "resolved", "reason_zh": "已保留历史口径并补明未扣交易成本。"}]}


class RepairTests(unittest.TestCase):
    def test_preserves_parent_veto_and_untouched_fields_without_mutation(self):
        m, card, veto, plan = fixture()
        before = copy.deepcopy((card, veto, plan))
        record = repair.revise(m, card, plan, veto)
        self.assertEqual((card, veto, plan), before)
        self.assertEqual(record["parent_veto"], veto)
        self.assertEqual(record["parent_card"], card)
        self.assertEqual(set(record["changes"]), {"facts"})
        self.assertEqual(record["card"]["title_zh"], card["title_zh"])
        self.assertNotEqual(record["parent_card_hash"], record["card_hash"])

    def test_stale_parent_or_missing_veto_fails(self):
        m, card, veto, plan = fixture()
        with self.assertRaisesRegex(ValueError, "parent veto"):
            repair.revise(m, card, plan)
        changed = copy.deepcopy(card); changed["questions"] = ["新的问题是什么？"]
        with self.assertRaisesRegex(ValueError, "stale revision parent"):
            repair.revise(m, changed, plan, veto)

    def test_changed_material_cannot_reuse_plan(self):
        m, card, veto, plan = fixture()
        m["paragraphs"][0]["text"] += " Altered."
        with self.assertRaisesRegex(ValueError, "material identity"):
            repair.revise(m, card, plan, veto)

    def test_noop_unknown_field_and_unaddressed_issue_fail(self):
        m, card, veto, plan = fixture()
        for mutation in (lambda p: p["edits"][0].update(value=card["facts"]),
                         lambda p: p["edits"][0].update(field="material_id"),
                         lambda p: p["issues"].append({"id": "other", "reason_zh": "还有一项未修订的问题。"})):
            p = copy.deepcopy(plan); mutation(p)
            with self.assertRaises(ValueError):repair.revise(m, card, p, veto)

    def test_source_ids_are_copied_and_unknown_ids_fail(self):
        m, card, veto, plan = fixture()
        record = repair.revise(m, card, plan, veto)
        self.assertEqual(record["card"]["facts"][0]["citations"][0]["quote"], cards.evidence_units(m)[0]["text"])
        plan["edits"][0]["value"][0]["evidence_ids"] = ["p1.s999"]
        with self.assertRaisesRegex(ValueError, "invalid evidence id"):
            repair.revise(m, card, plan, veto)

    def test_model_pass_still_requires_new_editorial_recheck(self):
        m, card, veto, plan = fixture(); record = repair.revise(m, card, plan, veto)
        review = review_for(m, record["card"])
        self.assertEqual(repair.evaluate(record, m, review)["status"], "awaiting_editorial_recheck")
        row = repair.evaluate(record, m, review, resolved(record))
        self.assertEqual(row["status"], "shadow_review_passed")
        self.assertEqual(row["external_fact_check"], "not_performed")
        self.assertEqual(row["mode"], "shadow_only")

    def test_old_review_invalidated_even_if_parent_was_approved(self):
        m, card, veto, plan = fixture(); record = repair.revise(m, card, plan, veto)
        row = repair.evaluate(record, m, review_for(m, card), resolved(record))
        self.assertEqual(row["status"], "review_invalid")
        self.assertIn("stale_review", row["errors"])

    def test_editor_cannot_override_model_rejection_or_missing_review(self):
        m, card, veto, plan = fixture(); record = repair.revise(m, card, plan, veto)
        disposition = resolved(record)
        self.assertEqual(repair.evaluate(record, m, None, disposition)["status"], "awaiting_semantic_review")
        review = review_for(m, record["card"])
        review["overall"] = "revise"; review["checks"][1]["verdict"] = "needs_revision"
        self.assertEqual(repair.evaluate(record, m, review, disposition)["status"], "semantic_revision_required")

    def test_editor_cannot_override_new_bad_number(self):
        m, card, veto, plan = fixture()
        plan["edits"][0]["value"][0]["text_zh"] = "作者报告历史收益为99%。"
        record = repair.revise(m, card, plan, veto)
        self.assertIn("unsupported_claim_number", record["evidence_errors"])
        self.assertEqual(repair.evaluate(record, m, None, resolved(record))["status"], "blocked_by_evidence")

    def test_incomplete_stale_or_duplicate_dispositions_fail(self):
        m, card, veto, plan = fixture(); record = repair.revise(m, card, plan, veto)
        for mutation in (lambda d: d.update(card_hash="old"), lambda d: d.update(checks=[]),
                         lambda d: d["checks"].append(copy.deepcopy(d["checks"][0]))):
            d = resolved(record); mutation(d)
            with self.assertRaises(ValueError):repair.evaluate(record, m, None, d)

    def test_new_editorial_rejection_still_blocks_model_pass(self):
        m, card, veto, plan = fixture(); record = repair.revise(m, card, plan, veto)
        disposition = resolved(record); disposition["checks"][0]["decision"] = "needs_revision"
        self.assertEqual(repair.evaluate(record, m, review_for(m, record["card"]), disposition)["status"], "editorial_revision_required")

    def test_tampered_ledger_cannot_replay(self):
        m, card, veto, plan = fixture(); record = repair.revise(m, card, plan, veto)
        record["parent_veto"] = None
        with self.assertRaises(ValueError):repair.evaluate(record, m)

    def test_cli_refuses_to_overwrite_another_revision(self):
        m, card, veto, plan = fixture(); mid = m["material_id"]
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)
            for name, data in (("materials", [m]), ("responses", {mid: card}), ("plans", {mid: plan}), ("vetoes", {mid: veto})):
                cards.write_json(p / (name + ".json"), data)
            argv = ["--materials", str(p/"materials.json"), "--responses", str(p/"responses.json"), "--plans", str(p/"plans.json"), "--editorial-vetoes", str(p/"vetoes.json"), "--out-dir", str(p/"out")]
            cards.write_json(p/"out/responses.json", {"existing": "unbound output"})
            with self.assertRaises(SystemExit):repair.main(argv)
            self.assertEqual(cards.read_json(p/"out/responses.json"), {"existing": "unbound output"})
            (p/"out/responses.json").unlink()
            self.assertEqual(repair.main(argv), 1)
            saved = (p/"out/revision-records.json").read_bytes()
            plan["issues"][0]["reason_zh"] = "修改了待解决问题的描述。"
            cards.write_json(p/"plans.json", {mid: plan})
            with self.assertRaises(SystemExit):repair.main(argv)
            self.assertEqual((p/"out/revision-records.json").read_bytes(), saved)


if __name__ == "__main__":
    unittest.main()
