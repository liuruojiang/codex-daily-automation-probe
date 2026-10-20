import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"scripts"))
import etf_card_repair as repair
import etf_content_cards as cards
import etf_repair_preview_probe as probe
from test_etf_automatic_mail_preview import two_articles, registry_for


def fixture():
    inputs = two_articles()
    bundle, _, manifest, history = inputs
    registry = registry_for(inputs)
    material = bundle["materials"][1]
    mid = material["material_id"]
    entry = registry["entries"][1]
    parent_card = copy.deepcopy(entry["card"])
    parent_card["title_zh"] = "原稿标题范围需修订"
    parent_full = cards.expand_evidence_ids(material, parent_card)
    parent_review = copy.deepcopy(entry["review"])
    parent_review["card_hash"] = cards.digest(parent_full)
    veto = {"material_id": mid, "card_hash": cards.digest(parent_full), "decision": "needs_revision", "reason_zh": "原稿标题范围需要修订。"}
    plan = {"material_id": mid, "parent_card_hash": cards.digest(parent_full), "parent_veto_hash": cards.digest(veto),
            "issues": [{"id": "scope", "reason_zh": veto["reason_zh"]}],
            "edits": [{"field": "title_zh", "value": entry["card"]["title_zh"], "issue_ids": ["scope"]}]}
    record = repair.revise(material, parent_full, plan, veto)
    disposition = {"revision_id": record["revision_id"], "card_hash": record["card_hash"],
                   "checks": [{"issue_id": "scope", "decision": "resolved", "reason_zh": "已重新核对来源标题范围。"}]}
    parent_registry = {**registry, "entries": registry["entries"][:1]}
    audit = {"version": probe.VERSION, "snapshot": registry["snapshot"], "parent_registry_sha256": cards.digest(parent_registry),
             "preserved_entry_sha256": [cards.digest(registry["entries"][0])], "review_batch": {}, "records": [
                 {"material_id": mid, "revision_id": record["revision_id"], "parent_card": parent_card,
                  "parent_review": parent_review, "parent_veto": veto, "plan": plan, "card_hash": record["card_hash"],
                  "review_hash": cards.digest(entry["review"]), "disposition": disposition, "editor_hash": cards.digest(entry["editor"])}]}
    return audit, registry, manifest, history


class RepairPreviewProbeTests(unittest.TestCase):
    def test_rebuilds_complete_revision_and_preserves_inputs_offline(self):
        values = fixture();before = copy.deepcopy(values)
        with patch.object(cards, "call_json", side_effect=AssertionError("no model")), patch.object(urllib.request, "urlopen", side_effect=AssertionError("no network")):
            result = probe.build(*values)
        self.assertEqual(values, before)
        self.assertEqual(result["parent_entries_preserved"], 1)
        self.assertEqual(result["repaired_entries_verified"], 1)
        self.assertTrue(result["items"][0]["original_editor_veto_preserved"])
        self.assertFalse(result["delivery_eligible"])

    def test_changed_existing_entry_is_not_accepted_as_carry_forward(self):
        values = fixture();values[1]["entries"][0]["card"]["title_zh"] = "被改动的旧稿"
        with self.assertRaisesRegex(ValueError, "preserved registry"):probe.build(*values)

    def test_rewritten_issue_reason_breaks_revision_identity(self):
        values = fixture();values[0]["records"][0]["plan"]["issues"][0]["reason_zh"] = "删掉了原否决理由。"
        with self.assertRaises(ValueError):probe.build(*values)

    def test_stale_parent_review_cannot_be_archived_as_the_original(self):
        values = fixture();values[0]["records"][0]["parent_review"]["card_hash"] = "different"
        with self.assertRaisesRegex(ValueError, "parent review"):probe.build(*values)

    def test_old_review_cannot_approve_changed_card(self):
        values = fixture();record = values[0]["records"][0];entry = values[1]["entries"][1]
        entry["review"] = copy.deepcopy(record["parent_review"])
        record["review_hash"] = cards.digest(entry["review"])
        with self.assertRaisesRegex(ValueError, "issue/model gates"):probe.build(*values)

    def test_every_original_issue_needs_a_fresh_disposition(self):
        values = fixture();values[0]["records"][0]["disposition"]["checks"] = []
        with self.assertRaisesRegex(ValueError, "incomplete editorial"):probe.build(*values)

    def test_stale_editor_hash_is_not_complete_editing(self):
        values = fixture();values[1]["entries"][1]["editor"]["card_hash"] = "stale"
        with self.assertRaisesRegex(ValueError, "current review/editor"):probe.build(*values)

    def test_original_editor_veto_cannot_be_omitted(self):
        values = fixture();values[0]["records"][0]["parent_veto"] = None
        with self.assertRaisesRegex(ValueError, "parent veto"):probe.build(*values)

    def test_duplicate_record_and_snapshot_drift_are_refused(self):
        values = fixture();values[0]["records"].append(copy.deepcopy(values[0]["records"][0]))
        with self.assertRaisesRegex(ValueError, "coverage mismatch"):probe.build(*values)
        values = fixture();values[0]["snapshot"] = {**values[0]["snapshot"], "body_sha256": "other"}
        with self.assertRaisesRegex(ValueError, "snapshot mismatch"):probe.build(*values)

    def test_an_unresolved_issue_cannot_pass_model_approval(self):
        values = fixture();values[0]["records"][0]["disposition"]["checks"][0]["decision"] = "needs_revision"
        with self.assertRaisesRegex(ValueError, "issue/model gates"):probe.build(*values)


if __name__ == "__main__":
    unittest.main()
