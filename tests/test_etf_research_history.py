"""Actual saved reviews: preserve historical decisions and hold new M01 failures."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import etf_automatic_mail_preview as automatic
import etf_content_cards as cards
import etf_research_history as history
import etf_supplement_research as paper
import etf_original_research as originals
import etf_research_resolution as crypto
import etf_section_research as sections

ROOT = Path(__file__).resolve().parents[1]


def real_records():
    names = {"paper": "etf-supplement-research-record.json", "original": "etf-original-research-records.json",
             "crypto": "etf-crypto-research-resolution.json", "sections": "etf-futures-section-research.json",
             "intake": "etf-source-intake-audit.json", "registry": "etf-shadow-reviewed-registry.json"}
    records = {role: cards.read_json(ROOT / "config" / name) for role, name in names.items()}
    materials = cards.read_json(ROOT / "tests/fixtures/etf_research_materials.json")
    return records, {m["material_id"]: m for m in materials}


class HistoricalResearchTests(unittest.TestCase):
    def test_five_actual_failed_entries_are_held_with_exact_claim_and_id_reasons(self):
        records, materials = real_records()
        expected = [(records["paper"]["entry"], {"facts.0": ["p20.s1"]}),
                    (records["original"]["entries"][0], {"limitations.0": ["p53.s2", "p53.s3"]}),
                    (records["original"]["entries"][2], {"facts.2": ["p50.s2"], "limitations.1": ["p47.s3"]}),
                    (records["crypto"]["entry"], {"limitations.1": ["p47.s3"]}),
                    (records["sections"]["entry"], {"facts.0": ["p12.s1"], "facts.2": ["p39.s2"], "facts.3": ["p56.s2"]})]
        before = copy.deepcopy(records)
        for entry, reasons in expected:
            material = materials[entry["material_id"]]
            card = automatic.decode_entry(entry, material)["cards"][material["material_id"]]
            with self.subTest(mid=material["material_id"], card_hash=entry["review"]["card_hash"]):
                result = history.historical_packet(material, card, entry["review"], entry["editor"])
                self.assertEqual(result["current_gate_status"], "held")
                self.assertEqual({r["claim_id"]: r["unowned_evidence_ids"] for r in result["current_gate_reasons"]}, reasons)
                self.assertTrue(result["historical_review_preserved"])
        self.assertEqual(records, before)

    def test_actual_builds_replay_history_without_approving_the_held_research(self):
        records, materials = real_records();before = copy.deepcopy(records)
        p, old, new, selected, registry = (records[k] for k in ("paper", "original", "crypto", "sections", "registry"))
        with patch.object(cards, "call_json", side_effect=AssertionError("model forbidden")):
            paper_result = paper.build(materials[p["entry"]["material_id"]], p, records["intake"], registry)
            original_result = originals.build([materials[e["material_id"]] for e in old["entries"]], old, p, registry)
            crypto_result = crypto.build(materials[new["entry"]["material_id"]], new, old, p, registry)
            section_result = sections.build(materials[selected["selection"]["parent_material_id"]], materials[selected["entry"]["material_id"]], selected, p, new, registry)
        for result in (paper_result, crypto_result, section_result):
            self.assertEqual(result["status"], "held")
            self.assertEqual(result["historical_recorded_status"], history.COMPLETE)
            self.assertEqual(result["current_gate_status"], "held")
            self.assertFalse(result["delivery_eligible"])
        self.assertEqual((original_result["research_completed"], original_result["research_held"]), (1, 2))
        self.assertEqual(original_result["items"][1]["current_gate_status"], "passed")
        self.assertEqual(original_result["items"][2]["historical_recorded_status"], old["source_review_status"][old["entries"][2]["material_id"]])
        self.assertEqual(crypto_result["original_model_overall"], "revise")
        self.assertEqual(records, before)

    def test_other_review_errors_and_stale_editor_still_raise_on_a_held_record(self):
        records, materials = real_records();entry = records["paper"]["entry"]
        material = materials[entry["material_id"]]
        card = automatic.decode_entry(entry, material)["cards"][material["material_id"]]
        for mutate in (lambda e: e["review"].update(card_hash="stale"),
                       lambda e: e["review"]["checks"].pop(),
                       lambda e: e["review"]["checks"][1].update(evidence_ids=["missing.s1"]),
                       lambda e: e["editor"].update(review_hash="stale"),
                       lambda e: e["editor"]["checks"].pop()):
            changed = copy.deepcopy(entry);mutate(changed)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                history.historical_packet(material, card, changed["review"], changed["editor"])


if __name__ == "__main__":
    unittest.main()
