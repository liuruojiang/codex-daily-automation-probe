import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import etf_automatic_mail_preview as auto
import etf_content_cards as cards
import etf_mail_replace_preview as replacement
from test_etf_mail_preview import fixture


def registry_for(inputs):
    bundle, metadata, manifest, history = inputs
    entries = []
    for material in bundle["materials"]:
        mid = material["material_id"];card = bundle["cards"][mid];units = cards.evidence_units(material)
        compact = {k: copy.deepcopy(v) for k, v in card.items() if k not in ("facts", "limitations")}
        for field in ("facts", "limitations"):
            compact[field] = [{"text_zh": claim["text_zh"], "evidence_ids": [next(unit["id"] for unit in units if unit["text"] == ref["quote"])
                                                                                     for ref in claim["citations"]]} for claim in card[field]]
        entries.append({"material_id": mid, "url": material["url"], "card": compact, "review": copy.deepcopy(bundle["reviews"][mid]),
                        "editor": copy.deepcopy(bundle["editors"][mid]), "annotation": copy.deepcopy(bundle["annotations"][mid])})
    return {"version": auto.REGISTRY_VERSION, "snapshot": auto.snapshot_identity(manifest, history), "entries": entries}


def two_articles():
    inputs = fixture();bundle, metadata, manifest, history = inputs
    first = bundle["materials"][0];old_id = first["material_id"]
    second = cards.make_material(title="Other synthetic portfolio research", url="https://example.org/other", source=first["source"],
                                 published=first["published"], paragraphs=[first["paragraphs"][0]["text"]])
    mid = second["material_id"];bundle["materials"].append(second)
    for key in ("cards", "reviews", "editors", "annotations"):
        bundle[key][mid] = copy.deepcopy(bundle[key][old_id]);bundle[key][mid]["material_id"] = mid
    bundle["reviews"][mid]["card_hash"] = cards.digest(bundle["cards"][mid])
    bundle["editors"][mid].update(card_hash=cards.digest(bundle["cards"][mid]), review_hash=cards.digest(bundle["reviews"][mid]))
    item = copy.deepcopy(manifest["candidates"][0]);item.update(title=second["title"], url=second["url"])
    manifest["candidates"].append(item);manifest["selected_items"].append(copy.deepcopy(item));manifest["history_added_items"].append(copy.deepcopy(item))
    manifest["source_audit"][0]["evidence"]["captured_count"] = 2
    metadata["body"] += "\n## Other section\n\n### Other synthetic portfolio research\n- 链接：https://example.org/other\n原来另一条正文。\n"
    metadata["html_body"] = auto.daily.markdown_to_email_html(metadata["body"], "synthetic")
    manifest["body_sha256"] = replacement.text_hash(metadata["body"])
    return inputs


class AutomaticPreviewTests(unittest.TestCase):
    def build(self, inputs, registry):
        return auto.build(inputs[1], inputs[2], inputs[3], registry)

    def test_complete_entry_is_expanded_from_source_ids_and_replaced_offline(self):
        inputs = fixture();registry = registry_for(inputs);original = copy.deepcopy((inputs, registry))
        self.assertNotIn('"quote"', __import__('json').dumps(registry))
        with patch.object(cards, "call_json", side_effect=AssertionError("no model")), patch.object(urllib.request, "urlopen", side_effect=AssertionError("no network")):
            report, email = self.build(inputs, registry)
        self.assertEqual((inputs, registry), original)
        self.assertEqual(report["replaced_count"], 1)
        self.assertEqual(report["registry_status"], "matched")
        self.assertEqual(report["items"][0]["status"], "replaced")
        self.assertFalse(report["delivery_eligible"])
        self.assertIn("作者报告12%的历史收益", email)

    def test_no_registry_still_produces_full_unchanged_email_and_clear_coverage(self):
        inputs = fixture();report, email = self.build(inputs, None)
        self.assertEqual(report["replaced_count"], 0)
        self.assertEqual(report["reason_counts"], {"no_reviewed_card": 1})
        self.assertEqual(report["preview_body_sha256"], report["original_body_sha256"])
        page = auto.render(report, email)
        self.assertIn("暂无可用核对稿，保留原文", page)
        self.assertIn("原始提醒与正文保持", page)
        self.assertIn("发送前缺漏检查：FAILED", page)

    def test_stale_snapshot_rejects_old_cards_even_with_same_url(self):
        for field in ("run_id", "head_sha", "cutoff_utc", "body_sha256", "history_sha256"):
            inputs = fixture();registry = registry_for(inputs);registry["snapshot"][field] = "other-version"
            report, _ = self.build(inputs, registry)
            with self.subTest(field=field):
                self.assertEqual(report["registry_status"], "stale")
                self.assertEqual(report["reason_counts"], {"registry_snapshot_mismatch": 1})
                self.assertEqual(report["preview_body_sha256"], report["original_body_sha256"])

    def test_invalid_editor_does_not_block_another_good_article(self):
        inputs = two_articles();registry = registry_for(inputs);registry["entries"][0]["editor"]["card_hash"] = "stale"
        report, email = self.build(inputs, registry)
        self.assertEqual(report["reason_counts"], {"invalid_review_record": 1, "replaced": 1})
        self.assertEqual(report["replaced_count"], 1)
        self.assertIn("原始提醒与正文保持", email)
        self.assertNotIn("原来另一条正文", email)

    def test_model_rejection_cannot_be_overridden_by_confirmed_editor(self):
        inputs = fixture();registry = registry_for(inputs);entry = registry["entries"][0]
        entry["review"]["overall"] = "revise";entry["review"]["checks"][-1]["verdict"] = "needs_revision"
        entry["editor"]["review_hash"] = cards.digest(entry["review"])
        report, email = self.build(inputs, registry)
        self.assertEqual(report["reason_counts"], {"review_not_complete": 1})
        self.assertNotIn("作者报告12%的历史收益", email)

    def test_missing_editor_is_awaiting_review_and_not_replaced(self):
        inputs = fixture();registry = registry_for(inputs);registry["entries"][0]["editor"] = None
        report, _ = self.build(inputs, registry)
        self.assertEqual(report["items"][0]["packet_status"], "awaiting_editorial_review")
        self.assertEqual(report["replaced_count"], 0)

    def test_duplicate_registry_urls_do_not_select_the_more_favorable_record(self):
        inputs = fixture();registry = registry_for(inputs);registry["entries"].append(copy.deepcopy(registry["entries"][0]))
        registry["entries"][1]["url"] += "?utm_source=other"
        report, _ = self.build(inputs, registry)
        self.assertEqual(report["reason_counts"], {"ambiguous_registry_entries": 1})
        self.assertTrue(all(row["status"] == "rejected" for row in report["registry_entries"]))

    def test_unknown_material_and_malformed_compact_card_are_explicitly_held(self):
        for change, expected in ((lambda e:e.update(material_id="old-material"), "stale_material"),
                                 (lambda e:e.update(material_id=[]), "stale_material"),
                                 (lambda e:e["card"]["facts"][0].update(evidence_ids=["unknown-unit"]), "invalid_review_record"),
                                 (lambda e:e["card"]["facts"][0].update(evidence_ids=123), "invalid_review_record"),
                                 (lambda e:e.pop("editor"), "invalid_review_record")):
            inputs = fixture();registry = registry_for(inputs);change(registry["entries"][0])
            report, _ = self.build(inputs, registry)
            self.assertEqual(report["reason_counts"], {expected: 1})

    def test_unselected_and_unsafe_registry_entries_never_add_articles(self):
        for value in ("https://example.org/not-selected", "javascript:alert(1)"):
            inputs = fixture();registry = registry_for(inputs);registry["entries"][0]["url"] = value
            report, email = self.build(inputs, registry)
            self.assertEqual(report["selected_count"], 1)
            self.assertEqual(report["reason_counts"], {"no_reviewed_card": 1})
            self.assertNotIn(value, email)
            self.assertNotEqual(report["registry_entries"][0]["status"], "used")

    def test_old_or_conflicting_date_keeps_original_despite_complete_review(self):
        for which in ("old", "conflicting"):
            inputs = fixture("2026-03-18T00:00:00+00:00" if which == "old" else "2026-10-19T12:00:00+00:00")
            registry = registry_for(inputs)
            if which == "conflicting":registry["entries"][0]["annotation"]["date_status"] = "conflicting"
            report, _ = self.build(inputs, registry)
            self.assertEqual(report["reason_counts"], {"date_history_held": 1})

    def test_missing_article_block_is_retained_with_reason(self):
        inputs = fixture();inputs[1]["body"] = inputs[1]["body"].replace("### Synthetic", "## Synthetic")
        inputs[1]["html_body"] = auto.daily.markdown_to_email_html(inputs[1]["body"], "")
        inputs[2]["body_sha256"] = replacement.text_hash(inputs[1]["body"])
        report, _ = self.build(inputs, registry_for(inputs))
        self.assertEqual(report["reason_counts"], {"identity_or_block_held": 1})

    def test_corrupt_source_or_registry_envelope_remains_fatal(self):
        inputs = fixture();registry = registry_for(inputs);inputs[1]["body"] += "changed"
        with self.assertRaises(ValueError):self.build(inputs, registry)
        inputs = fixture();registry = registry_for(inputs);registry["version"] = "unsupported"
        with self.assertRaisesRegex(ValueError, "registry envelope"):self.build(inputs, registry)

    def test_combined_composition_failure_cannot_be_reported_as_zero_coverage_success(self):
        inputs = fixture();registry = registry_for(inputs)
        first_result = replacement.build(inputs[0], inputs[1], inputs[2], inputs[3])
        with patch.object(replacement, "build", side_effect=[first_result, ValueError("synthetic final composition failure")]):
            with self.assertRaisesRegex(ValueError, "final composition failure"):self.build(inputs, registry)

    def test_cli_saves_zero_coverage_preview_without_sender_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder);inputs = fixture()
            for name, value in zip(("metadata", "manifest", "history"), inputs[1:]):cards.write_json(root/(name+".json"), value)
            args = ["--metadata", str(root/"metadata.json"), "--manifest", str(root/"manifest.json"), "--history-before", str(root/"history.json"), "--out-dir", str(root/"out")]
            self.assertEqual(auto.main(args), 0)
            self.assertEqual({p.name for p in (root/"out").iterdir()}, {"automatic-mail-preview.html", "automatic-mail-coverage.json"})
            with self.assertRaises(ValueError):auto.main(args)

    def test_shadow_workflow_saves_mail_and_coverage_without_keys_or_sending(self):
        root = Path(__file__).resolve().parents[1]
        text = (root/".github/workflows/etf-shadow-digest.yml").read_text(encoding="utf-8")
        workflow = yaml.load(text, Loader=yaml.BaseLoader)
        self.assertEqual(workflow["permissions"], {"contents": "read", "actions": "read"})
        self.assertNotIn("secrets.", text);self.assertNotIn("send_report", text)
        steps = workflow["jobs"]["shadow"]["steps"]
        self.assertTrue(any("etf_automatic_mail_preview.py" in step.get("run", "") for step in steps))
        upload = next(step for step in steps if step.get("with", {}).get("name") == "etf-shadow-mail-preview")
        self.assertEqual(upload["if"], "always()")
        self.assertEqual(upload["with"]["retention-days"], "30")
        regression = yaml.load((root/".github/workflows/delivery-regression.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        job = regression["jobs"]["etf-automatic-mail-preview"]
        self.assertEqual(job["permissions"], {"contents": "read", "actions": "read"})
        self.assertIn("head.repo.full_name == github.repository", job["if"])
        self.assertTrue(any("etf_automatic_preview_probe.py" in step.get("run", "") for step in job["steps"]))


if __name__ == "__main__":
    unittest.main()
