import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"scripts"))
import etf_content_cards as cards
import etf_original_research as research
import etf_semantic_review as semantic
from test_etf_supplement_research import fixture as previous_fixture


def fixture():
    material, previous, _, registry = previous_fixture()
    mid = material["material_id"];source = previous["original_sources"][0]["extraction"]
    source.update(material_id=mid, url=material["url"], actual_title=material["title"],
                  published_raw=material["published"], paragraph_text_sha256=cards.digest([p["text"] for p in material["paragraphs"]]),
                  pending_flags=[], tables_and_figures_extracted=False,
                  extraction_spec={"title_selector": "h1", "expected_title": material["title"], "body_selector": "article"})
    entry = copy.deepcopy(previous["entry"]);ledger = copy.deepcopy(previous["repair"])
    ledger["parent_evaluation"] = semantic.evaluate(material, cards.expand_evidence_ids(material, ledger["parent_card"]), ledger["parent_review"])
    record = {"version": research.VERSION, "parent_research_sha256": cards.digest(previous), "snapshot": registry["snapshot"],
              "registry_sha256": cards.digest(registry), "delivery_eligible": False, "mail_approvals_added": 0,
              "external_fact_check": "not_performed", "entries": [entry], "repairs": {mid: ledger},
              "initial_card_sha256": {mid: cards.digest(cards.expand_evidence_ids(material, ledger["parent_card"]))},
              "source_review_status": {mid: "source_review_complete_for_research_preview"}}
    return [material], record, previous, registry


def hold(values, editor_revises=True):
    _, record, _, _ = values;entry = record["entries"][0];mid = entry["material_id"]
    entry["review"]["overall"] = "revise"
    entry["review"]["checks"][1]["verdict"] = "needs_revision"
    entry["editor"]["review_hash"] = cards.digest(entry["review"])
    if editor_revises:entry["editor"]["checks"][1]["decision"] = "needs_revision"
    record["source_review_status"][mid] = "editorial_revision_required" if editor_revises else "semantic_resolution_required"


class OriginalResearchTests(unittest.TestCase):
    def test_replay_keeps_old_records_unchanged_without_network_or_models(self):
        values = fixture();before = copy.deepcopy(values)
        with patch.object(cards, "call_json", side_effect=AssertionError("no model")), patch.object(research.urllib.request, "urlopen", side_effect=AssertionError("no fetch")):
            first = research.build(*values);second = research.build(*values)
        self.assertEqual(first, second);self.assertEqual(values, before)
        self.assertEqual(first["research_completed"], 1)
        self.assertFalse(first["delivery_eligible"]);self.assertEqual(first["mail_approvals_added"], 0)

    def test_failed_model_and_editor_are_explicitly_held(self):
        values = fixture();hold(values)
        proof = research.build(*values)
        self.assertEqual((proof["research_completed"], proof["research_held"]), (0, 1))
        self.assertEqual(proof["items"][0]["unresolved_model_claims"], ["facts.0"])

    def test_editor_confirmation_cannot_override_model_rejection(self):
        values = fixture();hold(values, False)
        proof = research.build(*values)
        self.assertEqual(proof["items"][0]["status"], "semantic_resolution_required")
        values[1]["source_review_status"][values[0][0]["material_id"]] = "source_review_complete_for_research_preview"
        with self.assertRaisesRegex(ValueError, "status inflated"):research.build(*values)

    def test_changed_old_research_or_registry_cannot_be_carried_forward(self):
        for index in (2, 3):
            values = fixture();values[index]["new"] = "changed"
            with self.subTest(index=index), self.assertRaises(ValueError):research.build(*values)

    def test_restricted_or_flagged_source_cannot_be_admitted(self):
        for update in ({"pending_flags": ["confirm_source_time_zone"]}, {"access_scope": "metadata_only_subscription_marker"},
                       {"tables_and_figures_extracted": True}, {"date_status": "timezone_unconfirmed"}):
            values = fixture();values[2]["original_sources"][0]["extraction"].update(update)
            values[1]["parent_research_sha256"] = cards.digest(values[2])
            with self.subTest(update=update), self.assertRaises(ValueError):research.build(*values)

    def test_no_directory_date_or_changed_body_can_replace_original_identity(self):
        for key in ("published_raw", "paragraph_text_sha256", "actual_title"):
            values = fixture();values[2]["original_sources"][0]["extraction"][key] = "changed"
            values[1]["parent_research_sha256"] = cards.digest(values[2])
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "body/date identity"):research.build(*values)

    def test_duplicate_and_incomplete_entries_are_rejected(self):
        for duplicate in (True, False):
            values = fixture()
            if duplicate:values[1]["entries"].append(copy.deepcopy(values[1]["entries"][0]))
            else:values[1]["entries"] = []
            with self.subTest(duplicate=duplicate), self.assertRaisesRegex(ValueError, "coverage mismatch"):research.build(*values)

    def test_stale_editor_cannot_complete_research(self):
        values = fixture();values[1]["entries"][0]["editor"]["card_hash"] = "stale"
        with self.assertRaisesRegex(ValueError, "editorial"):research.build(*values)

    def test_changed_card_cannot_drop_its_original_repair_history(self):
        values = fixture();values[1]["repairs"] = {}
        with self.assertRaisesRegex(ValueError, "lost its repair history"):research.build(*values)

    def test_original_refusal_and_veto_must_be_preserved(self):
        for field in ("parent_evaluation", "parent_veto"):
            values = fixture();ledger = next(iter(values[1]["repairs"].values()));ledger[field] = None
            with self.subTest(field=field), self.assertRaises(ValueError):research.build(*values)

    def test_unresolved_original_writing_issue_stays_held(self):
        values = fixture();ledger = next(iter(values[1]["repairs"].values()))
        ledger["disposition"]["checks"][0]["decision"] = "needs_revision"
        # A model pass cannot turn an unresolved issue into completed research.
        with self.assertRaisesRegex(ValueError, "issue/model gates"):research.build(*values)

    def test_research_cannot_claim_mail_or_external_fact_approval(self):
        for update in ({"delivery_eligible": True}, {"mail_approvals_added": 1}, {"external_fact_check": "verified"}):
            values = fixture();values[1].update(update)
            with self.subTest(update=update), self.assertRaisesRegex(ValueError, "approve mail"):research.build(*values)

    def test_live_body_drift_does_not_silently_update_the_record(self):
        values = fixture();saved = values[2]["original_sources"][0]["extraction"]
        with patch.object(research, "fetch_source", return_value=b"public response"), patch.object(research.original, "extract", return_value=(values[0][0], {**saved, "published_raw": "changed"})):
            with self.assertRaisesRegex(ValueError, "extraction changed"):research.rebuild_sources(values[2])


if __name__ == "__main__":unittest.main()
