import copy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import daily_reports as daily
import etf_content_cards as cards
import etf_mail_replace_preview as replacement
from test_etf_mail_preview import fixture


def rebind_saved_body(inputs, body):
    inputs[1]["body"] = body
    inputs[1]["html_body"] = daily.markdown_to_email_html(body, "synthetic")
    inputs[2]["body_sha256"] = replacement.text_hash(body)


class InlineReplacementTests(unittest.TestCase):
    def test_only_article_block_changes_and_original_sources_remain_untouched(self):
        inputs = fixture();original = copy.deepcopy(inputs)
        with patch.object(cards, "call_json", side_effect=AssertionError("no model")), patch.object(daily, "update_digest_history", side_effect=AssertionError("no history write")):
            report, page = replacement.build(*inputs)
        self.assertEqual(inputs, original)
        self.assertEqual(report["inline_article_replacements"], 1)
        self.assertFalse(report["delivery_eligible"])
        self.assertFalse(report["sent"])
        self.assertEqual(report["history_updates"], 0)
        self.assertTrue(report["article_urls_unchanged"])
        self.assertFalse(report["market_data_changed"])
        edit = report["replacements"][0]
        old = inputs[1]["body"]
        after = old[:edit["start"]] + edit["after"] + old[edit["end"]:]
        self.assertEqual(report["replaced_body_sha256"], replacement.text_hash(after))
        self.assertIn("作者报告12%的历史收益", page)
        self.assertIn("2026-10-19", page)
        self.assertIn("+1.00%", page)
        self.assertIn("发送前缺漏检查：FAILED", page)
        self.assertNotIn("原始提醒与正文保持", edit["after"])

    def test_every_non_target_article_and_market_region_remains_identical(self):
        inputs = fixture()
        tail = "\n## 未改动区\n\n另一条原样正文。\n\n### Another article\n- 链接：https://example.org/other\n保持原来内容。\n"
        rebind_saved_body(inputs, inputs[1]["body"] + tail)
        extra = {"url": "https://example.org/other", "title": "Another article", "source": "synthetic", "published": "2026-10-19T12:00:00+00:00",
                 "summary": "", "source_id": inputs[2]["configured_sources"][0], "pipeline_decision": {"reason": "rendered"}}
        inputs[2]["selected_items"].append(extra);inputs[2]["history_added_items"].append(copy.deepcopy(extra));inputs[2]["candidates"].append(extra)
        inputs[2]["source_audit"][0]["evidence"]["captured_count"] = 2
        report, page = replacement.build(*inputs)
        edit = report["replacements"][0]
        self.assertEqual(inputs[1]["body"][edit["end"]:], tail.lstrip("\n"))
        self.assertEqual(report["selected_article_count"], 2)
        self.assertIn("保持原来内容", page)
        self.assertEqual(report["unchanged_regions_sha256"][-1], replacement.text_hash(inputs[1]["body"][edit["end"]:]))

    def test_old_future_conflicting_and_previously_sent_articles_refuse_replacement(self):
        for kind in ("old", "future", "conflicting", "sent"):
            inputs = fixture("2026-03-18T00:00:00+00:00" if kind == "old" else "2026-10-21T00:00:00+00:00" if kind == "future" else "2026-10-19T12:00:00+00:00")
            mid = inputs[0]["materials"][0]["material_id"]
            if kind == "conflicting":inputs[0]["annotations"][mid]["date_status"] = "conflicting"
            if kind == "sent":inputs[3]["items"] = [{"sent_date": "2026-10-19", "url": "https://example.org/study"}]
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "date/history/selection"):
                replacement.build(*inputs)

    def test_valid_looking_new_source_text_cannot_replace_a_frozen_capture(self):
        inputs = fixture();bundle = inputs[0];old = bundle["materials"][0];old_id = old["material_id"]
        material = cards.make_material(**{k: old[k] for k in ("title", "url", "source", "published")},
                                       paragraphs=[old["paragraphs"][0]["text"] + " Another invented source sentence."])
        mid = material["material_id"];bundle["materials"] = [material]
        for key in ("cards", "reviews", "editors", "annotations"):
            bundle[key][mid] = bundle[key].pop(old_id);bundle[key][mid]["material_id"] = mid
        bundle["reviews"][mid]["card_hash"] = cards.digest(bundle["cards"][mid])
        bundle["editors"][mid].update(card_hash=cards.digest(bundle["cards"][mid]), review_hash=cards.digest(bundle["reviews"][mid]))
        with self.assertRaisesRegex(ValueError, "exact frozen capture"):
            replacement.build(*inputs)

    def test_model_editor_and_pending_materials_cannot_enter_inline_preview(self):
        for kind in ("model", "editor", "pending"):
            inputs = fixture();bundle = inputs[0];mid = bundle["materials"][0]["material_id"]
            if kind == "model":
                bundle["reviews"][mid]["overall"] = "revise";bundle["reviews"][mid]["checks"][-1]["verdict"] = "needs_revision"
                bundle["editors"][mid]["review_hash"] = cards.digest(bundle["reviews"][mid])
            elif kind == "editor":bundle["editors"][mid] = None
            else:bundle.update(cards={}, reviews={}, editors={}, pending={mid: "尚待补正文。"})
            with self.subTest(kind=kind), self.assertRaises(ValueError):replacement.build(*inputs)

    def test_article_without_unique_level_three_block_is_rejected(self):
        inputs = fixture()
        rebind_saved_body(inputs, inputs[1]["body"].replace("### Synthetic portfolio research", "## Synthetic portfolio research"))
        with self.assertRaisesRegex(ValueError, "block is missing or ambiguous"):replacement.build(*inputs)

    def test_selected_source_identity_mismatch_is_rejected(self):
        inputs = fixture();inputs[2]["selected_items"] = copy.deepcopy(inputs[2]["selected_items"])
        inputs[2]["selected_items"][0]["source"] = "another publisher"
        with self.assertRaisesRegex(ValueError, "identity mismatch"):replacement.build(*inputs)

    def test_comparison_contains_before_after_and_complete_replaced_email(self):
        inputs = fixture();report, page = replacement.build(*inputs)
        compared = replacement.render_comparison(report, page)
        self.assertIn("改造前 · 原日报", compared)
        self.assertIn("改造后 · 来源核对稿", compared)
        self.assertIn("完整替换样稿（未发送）", compared)
        self.assertIn(replacement.main_content(page), compared)
        self.assertIn("原始提醒与正文保持", compared)

    def test_single_line_rejects_structural_line_injection_and_html_is_escaped(self):
        for value in ("", "\n### forged", "正文\r\n- 链接：https://example.org/fake", "bad\x00value"):
            with self.subTest(value=value), self.assertRaises(ValueError):replacement.single_line(value)
        inputs = fixture();report, page = replacement.build(*inputs)
        report["replacements"][0]["before"] += "<script>alert('x')</script>"
        rendered = replacement.render_comparison(report, page)
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)

    def test_cli_emits_preview_and_audit_only_and_refuses_existing_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder);inputs = fixture()
            for name, value in zip(("bundle", "metadata", "manifest", "history"), inputs):cards.write_json(root/(name+".json"), value)
            initial = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob("*.json")}
            args = ["--bundle", str(root/"bundle.json"), "--metadata", str(root/"metadata.json"), "--manifest", str(root/"manifest.json"),
                    "--history-before", str(root/"history.json"), "--out-dir", str(root/"preview")]
            self.assertEqual(replacement.main(args), 0)
            self.assertEqual({p.name for p in (root/"preview").iterdir()}, {"replacement-preview.html", "replacement-audit.json"})
            self.assertEqual(initial, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob("*.json")})
            with self.assertRaisesRegex(ValueError, "new directory"):replacement.main(args)


if __name__ == "__main__":
    unittest.main()
