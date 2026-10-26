import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"scripts"))
import etf_content_cards as cards
import etf_supplement_research as research
from test_etf_repair_preview_probe import fixture as repair_fixture


def fixture():
    audit, registry, manifest, _ = repair_fixture()
    old = audit["records"][0];entry = registry["entries"][1]
    material = next(m for m in cards.prepare_manifest(manifest) if m["material_id"] == entry["material_id"])
    lead = {"url": "https://example.org/original", "parent_material_id": "directory"}
    source = {"url": lead["url"], "parent_directory_material_id": "directory", "raw_sha256": "a"*64,
              "published_raw": "2026-10-18T10:00:00Z", "date_basis": "JSON-LD datePublished", "date_status": "source_timestamp_with_zone",
              "access_scope": "public_html_excerpt", "material_id": "new-source", "paragraph_count": 2, "text_chars": 300,
              "complete_article_claim": False, "delivery_eligible": False, "mail_approvals_added": 0, "review_status": "pending"}
    intake = {"snapshot": registry["snapshot"], "registry_sha256": cards.digest(registry), "source_leads": [lead],
              "supplements": [{"material_id": material["material_id"], "original_published": material["published"]}]}
    record = {"version": research.VERSION, "snapshot": intake["snapshot"], "intake_audit_sha256": cards.digest(intake),
              "registry_sha256": cards.digest(registry), "entry": copy.deepcopy(entry),
              "repair": {key: old[key] for key in ("parent_card", "parent_review", "parent_veto", "plan", "revision_id", "disposition")},
              "original_sources": [{"requested_url": lead["url"], "response_status": 200, "body_sha256": "a"*64,
                                    "started_utc": "2026-10-21T00:00:00+00:00", "finished_utc": "2026-10-21T00:00:01+00:00", "extraction": source}],
              "delivery_eligible": False, "frozen_mail_admission": "held_later_capture"}
    return material, record, intake, registry


class SupplementResearchTests(unittest.TestCase):
    def test_complete_research_remains_held_and_inputs_are_unchanged(self):
        values = fixture();before = copy.deepcopy(values)
        with patch.object(cards, "call_json", side_effect=AssertionError("no model")):proof = research.build(*values)
        self.assertEqual(values, before)
        self.assertEqual(proof["status"], "source_review_complete_for_research_preview")
        self.assertEqual(proof["public_excerpts_prepared"], 1)
        self.assertFalse(proof["delivery_eligible"])
        self.assertEqual(proof["frozen_mail_admission"], "held_later_capture")

    def test_changed_intake_registry_or_delivery_state_is_refused(self):
        for mutate in (lambda v:v[2].update(extra="changed"), lambda v:v[3]["entries"][0].update(url="https://example.org/changed"),
                       lambda v:v[1].update(delivery_eligible=True), lambda v:v[1].update(frozen_mail_admission="allowed")):
            values = fixture();mutate(values)
            with self.subTest(), self.assertRaises(ValueError):research.build(*values)

    def test_original_review_cannot_approve_the_repaired_card(self):
        values = fixture();values[1]["entry"]["review"] = copy.deepcopy(values[1]["repair"]["parent_review"])
        with self.assertRaisesRegex(ValueError, "repair gates"):research.build(*values)

    def test_unresolved_writing_issue_cannot_be_hidden_by_model_pass(self):
        values = fixture();values[1]["repair"]["disposition"]["checks"][0]["decision"] = "needs_revision"
        with self.assertRaisesRegex(ValueError, "repair gates"):research.build(*values)

    def test_stale_editor_cannot_complete_source_review(self):
        values = fixture();values[1]["entry"]["editor"]["card_hash"] = "stale"
        with self.assertRaisesRegex(ValueError, "editorial"):research.build(*values)

    def test_original_editor_veto_cannot_be_removed(self):
        values = fixture();values[1]["repair"]["parent_veto"] = None
        with self.assertRaisesRegex(ValueError, "parent veto"):research.build(*values)

    def test_unreviewed_sources_cannot_inherit_dates_or_expose_restricted_body(self):
        for mutate in (lambda x:x.update(date_basis="directory date"), lambda x:x.update(published_raw="2026-10-18T10:00:00"),
                       lambda x:x.update(review_status="approved"), lambda x:x.update(access_scope="metadata_only_subscription_marker"),
                       lambda x:x.update(raw_sha256="changed")):
            values = fixture();mutate(values[1]["original_sources"][0]["extraction"])
            with self.subTest(), self.assertRaises(ValueError):research.build(*values)


if __name__ == "__main__":unittest.main()
