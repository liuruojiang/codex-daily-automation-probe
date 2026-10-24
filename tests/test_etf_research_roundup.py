import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"scripts"))
import etf_content_cards as cards
import etf_research_roundup as roundup


def fixture():
    def entry(mid, verdict="pass", hash_value=None):
        return {"material_id": mid, "url": "https://example.org/"+mid,
                "card": {"title_zh": mid, "facts": [], "limitations": [], "questions": []},
                "review": {"overall": verdict, "card_hash": hash_value or mid+"-card"}}
    old = {"entries": [entry("sharpe"), entry("liquidity"), entry("crypto", "revise", "old-crypto")],
           "source_review_status": {"sharpe": "source_review_complete_for_research_preview",
                                    "liquidity": "source_review_complete_for_research_preview", "crypto": "editorial_revision_required"}}
    records = {"intake": {"source": "synthetic"}, "paper": {"entry": entry("paper")}, "original": old,
               "crypto_resolution": {"entry": entry("crypto", hash_value="new-crypto"), "prior_research_sha256": cards.digest(old),
                                     "repair": {"plan": {"parent_card_hash": "old-crypto"}}}, "sections": {"entry": entry("futures")}}
    registry = {"snapshot": {"cutoff_utc": "2026-10-20T00:28:29+00:00"}, "entries": []}
    index = make_index(records, registry)
    return records, registry, index


def make_index(records, registry):
    return {"version": roundup.VERSION, "record_sha256": {role: cards.digest(records[role]) for role in roundup.FILES},
            "registry_sha256": cards.digest(registry), "snapshot": copy.deepcopy(registry["snapshot"]),
            "delivery_eligible": False, "external_fact_check": "not_performed", "entries": [
                {"record": role, "material_id": entry["material_id"], "entry_sha256": cards.digest(entry), "annotation": {
                    "material_id": entry["material_id"], "date_status": "captured", "date_note_zh": "保持原始时间字段。", "scope_note_zh": "仅为来源节选。"}}
                for role, entry in roundup.current_entries(records)]}


class RoundupTests(unittest.TestCase):
    def test_explicit_resolution_replaces_same_material_once_and_keeps_old_refusal(self):
        records, registry, index = fixture();before = copy.deepcopy(records)
        entries = roundup.resolve_entries(records, registry, index)
        self.assertEqual(len(entries), 5)
        crypto = [e for role, e in entries if e["material_id"] == "crypto"]
        self.assertEqual(len(crypto), 1);self.assertEqual(crypto[0]["review"]["card_hash"], "new-crypto")
        self.assertEqual(records, before);self.assertEqual(records["original"]["entries"][-1]["review"]["overall"], "revise")

    def test_any_old_research_record_change_invalidates_index(self):
        for role in roundup.FILES:
            values = fixture();values[0][role]["extra"] = "changed"
            with self.subTest(role=role), self.assertRaisesRegex(ValueError, "fingerprint"):roundup.resolve_entries(*values)

    def test_no_implicit_latest_or_new_material_append_can_replace_missing_parent(self):
        records, _, _ = fixture();records["original"]["entries"].pop()
        records["crypto_resolution"]["prior_research_sha256"] = cards.digest(records["original"])
        with self.assertRaisesRegex(ValueError, "exact held-parent"):roundup.current_entries(records)

    def test_resolution_requires_exact_rejected_card_not_another_pass(self):
        for mutate in (lambda r:r["crypto_resolution"]["repair"]["plan"].update(parent_card_hash="different"),
                       lambda r:r["original"]["entries"][-1]["review"].update(overall="pass"),
                       lambda r:r["original"]["source_review_status"].update(crypto="source_review_complete_for_research_preview")):
            records, _, _ = fixture();mutate(records)
            records["crypto_resolution"]["prior_research_sha256"] = cards.digest(records["original"])
            with self.subTest(), self.assertRaisesRegex(ValueError, "exact held-parent"):roundup.current_entries(records)

    def test_duplicate_source_urls_cannot_be_counted_twice(self):
        records, _, _ = fixture();records["sections"]["entry"]["url"] = records["paper"]["entry"]["url"]
        with self.assertRaisesRegex(ValueError, "duplicate"):roundup.current_entries(records)

    def test_missing_duplicate_or_reordered_index_entries_are_refused(self):
        for mutate in (lambda i:i["entries"].pop(), lambda i:i["entries"].reverse(),
                       lambda i:i["entries"].__setitem__(1, copy.deepcopy(i["entries"][0]))):
            values = fixture();mutate(values[2])
            with self.subTest(), self.assertRaises(ValueError):roundup.resolve_entries(*values)

    def test_stale_entry_digest_or_wrong_record_cannot_select_another_version(self):
        for update in ({"entry_sha256": "stale"}, {"record": "paper"}, {"material_id": "unknown"}):
            values = fixture();values[2]["entries"][2].update(update)
            with self.subTest(update=update), self.assertRaisesRegex(ValueError, "exact entry/order"):roundup.resolve_entries(*values)

    def test_registry_or_snapshot_drift_is_refused(self):
        values = fixture();values[1]["entries"].append({"changed": True})
        with self.assertRaisesRegex(ValueError, "fingerprint"):roundup.resolve_entries(*values)
        values = fixture();values[2]["snapshot"]["cutoff_utc"] = "2026-10-21T00:00:00Z"
        with self.assertRaisesRegex(ValueError, "snapshot"):roundup.resolve_entries(*values)

    def test_invalid_source_date_annotations_are_not_silently_guessed(self):
        for update in ({"date_status": "verified"}, {"material_id": "other"}, {"scope_note_zh": ""}):
            values = fixture();values[2]["entries"][0]["annotation"].update(update)
            with self.subTest(update=update), self.assertRaisesRegex(ValueError, "annotation"):roundup.resolve_entries(*values)

    def test_delivery_or_external_fact_approval_is_forbidden(self):
        for update in ({"delivery_eligible": True}, {"external_fact_check": "verified"}):
            values = fixture();values[2].update(update)
            with self.subTest(update=update), self.assertRaises(ValueError):roundup.resolve_entries(*values)

    def test_offline_source_inputs_must_be_supplied_together(self):
        for paths in ((Path("captures"), None), (None, Path("paper.html"))):
            with self.subTest(paths=paths), self.assertRaisesRegex(ValueError, "requires both"):
                roundup.load_materials({}, {}, *paths)

    def test_render_escapes_claims_and_preserves_entire_existing_email(self):
        baseline = '<html><head></head><body><main class="email-main"><p>UNCHANGED_EMAIL_WARNING</p></main></body></html>'
        item = {"card": {"title_zh": '<script>alert(1)</script>', "facts": [{"text_zh": '<b>原文陈述</b>'}],
                         "limitations": [{"text_zh": "限制说明。"}], "questions": ["后续问题？"]},
                "source": "example.org", "published": "2026-10-18T15:50:46+00:00", "editorial_claim_count": 4,
                "date_gate": {"window": "primary_window"}, "frozen_mail_reason": "not_selected", "url": "https://example.org/source",
                "annotation": {"scope_note_zh": "节选范围。", "date_note_zh": "保留原时间。"}}
        report = {"research_count": 1, "mail_selected_count": 22, "mail_replacements_remain": 10, "mail_retained_count": 12,
                  "snapshot_report_date": "2026-10-20", "research_items": [item]}
        rendered = roundup.render(report, baseline)
        self.assertIn('&lt;script&gt;', rendered);self.assertNotIn('<script>', rendered)
        self.assertIn('&lt;b&gt;原文陈述&lt;/b&gt;', rendered)
        self.assertEqual(rendered.count('UNCHANGED_EMAIL_WARNING'), 1)
        self.assertIn('1份独立研究', rendered);self.assertIn('字段位于36小时内', rendered)

    def test_held_historical_card_is_explicitly_separate_from_current_passed_research(self):
        baseline = '<html><head></head><body><main class="email-main"><p>ORIGINAL</p></main></body></html>'
        item = {"historical_card": {"title_zh": "历史稿标题", "facts": [{"text_zh": "历史论断保留。"}],
                "limitations": [], "questions": []}, "source": "fixture", "published": "2026-10-18", "editorial_claim_count": 4,
                "current_gate_status": "held", "historical_review_hash": "preserved-review-hash",
                "date_gate": {"window": "primary_window"}, "frozen_mail_reason": "not_selected", "url": "https://example.org/source",
                "annotation": {"scope_note_zh": "节选。", "date_note_zh": "保留日期。"}}
        report = {"research_count": 1, "mail_selected_count": 22, "mail_replacements_remain": 10, "mail_retained_count": 12,
                  "snapshot_report_date": "2026-10-20", "research_items": [item],
                  "current_gate_held_count": 1, "current_gate_passed_count": 0}
        rendered = roundup.render(report, baseline)
        self.assertIn("当前校验通过0份，待重新复核1份", rendered)
        self.assertIn("历史内容不计入当前通过稿", rendered)
        self.assertIn("preserved-review-hash", rendered)
        self.assertIn("历史论断保留。", rendered)
        self.assertEqual(rendered.count("ORIGINAL"), 1)

    def test_real_five_versions_keep_four_new_holds_and_one_pass_without_rewriting_history(self):
        from etf_archived_source import verified_archive
        root = Path(__file__).resolve().parents[1]
        records = {role:cards.read_json(root/"config"/name) for role,name in roundup.FILES.items()}
        registry = cards.read_json(root/"config/etf-shadow-reviewed-registry.json")
        index = cards.read_json(root/"config/etf-research-preview-index.json")
        all_materials = cards.read_json(root/"tests/fixtures/etf_research_materials.json")
        by_id = {m["material_id"]:m for m in all_materials}
        wanted = [e["material_id"] for _,e in roundup.current_entries(records)]
        parent = by_id[records["sections"]["selection"]["parent_material_id"]]
        archive, _ = verified_archive("baseline")
        metadata,manifest,history = [json.loads(archive.read(n)) for n in
            ("metadata.json","collection_manifest.json","history_before.json")]
        before = cards.digest([records,registry,index,all_materials])
        report, baseline = roundup.build([by_id[mid] for mid in wanted], parent, records, registry,index,metadata,manifest,history)
        self.assertEqual((report["research_count"],report["current_gate_held_count"],report["current_gate_passed_count"]),(5,4,1))
        self.assertEqual((report["mail_selected_count"],report["mail_replacements_remain"],report["mail_retained_count"]),(22,10,12))
        self.assertTrue(all(not row["delivery_eligible"] and row["historical_review_preserved"] for row in report["research_items"]))
        self.assertEqual(sum("historical_card" in row and "card" not in row for row in report["research_items"]),4)
        self.assertEqual(cards.digest([records,registry,index,all_materials]),before)
        rendered = roundup.render(report,baseline)
        self.assertIn("当前校验通过1份，待重新复核4份",rendered)
        self.assertEqual(rendered.count("以下历史内容不计入当前通过稿"),4)


if __name__ == "__main__":unittest.main()
