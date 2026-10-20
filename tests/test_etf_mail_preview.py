import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import daily_reports as daily
import etf_content_cards as cards
import etf_mail_preview as preview
import etf_semantic_review as semantic

TEXT = ("The study reports a 12% historical return before transaction costs and uses a limited sample of portfolio observations. "
        "The authors explain that these observations cannot establish future performance and that implementation costs have not been tested.")


def fixture(published="2026-10-19T12:00:00+00:00", source_id="research|Example|https://example.org/feed"):
    material = cards.make_material(title="Synthetic portfolio research", url="https://example.org/study", source="synthetic",
                                   published=published, paragraphs=[TEXT])
    mid = material["material_id"]
    card = cards.expand_evidence_ids(material, {"material_id": mid, "title_zh": "合成组合的历史收益研究",
            "facts": [{"text_zh": "作者报告12%的历史收益，未扣交易成本。", "evidence_ids": ["p1.s1"]}],
            "limitations": [], "questions": ["样本外表现如何？"]})
    review = {"material_id": mid, "card_hash": cards.digest(card), "overall": "pass", "checks": [
        {"claim_id": cid, "verdict": "supported", "reason_zh": "该项与原文一致。", "evidence_ids": ["p1.s1"] if cid.startswith("facts.") else []}
        for cid in semantic.claim_map(card)]}
    editor = {"material_id": mid, "card_hash": cards.digest(card), "review_hash": cards.digest(review), "appeal_case_id": None,
              "checks": [{"claim_id": cid, "decision": "confirmed", "reason_zh": "按保存原文逐项核对。", "evidence_ids": ["p1.s1"] if cid.startswith("facts.") else []}
                         for cid in semantic.claim_map(card)]}
    bundle = {"version": preview.INPUT_VERSION, "materials": [material], "cards": {mid: card}, "reviews": {mid: review}, "editors": {mid: editor},
              "annotations": {mid: {"material_id": mid, "date_status": "captured", "date_note_zh": "保留保存发布日期。", "scope_note_zh": "仅核对保存节选，不是全文。"}},
              "pending": {}, "appeals": {}}
    item = {k: material[k] for k in ("title", "url", "source", "published")}
    item.update(summary=TEXT, source_id=source_id, pipeline_decision={"reason": "rendered"}, independent_eligible=True)
    body = "# 美股 ETF 与资产配置日报 - 2026-10-20\n\n发送前缺漏检查：FAILED\n\n## 原行情\n\n|代码|交易日|涨跌幅|\n|---|---|---|\n|TEST|2026-10-19|+1.00%|\n\n### Synthetic portfolio research\n- 链接：https://example.org/study\n原始提醒与正文保持。\n"
    metadata = {"subject": "美股 ETF 与资产配置日报 - 2026-10-20", "body": body, "attachment": None,
                "html_body": daily.markdown_to_email_html(body, "synthetic")}
    manifest = {"schema_version": 2, "decision_policy": "etf-candidates-v1", "run_id": "synthetic-run", "head_sha": "synthetic-sha",
                "cutoff_utc": "2026-10-20T00:00:00+00:00", "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
                "selected_items": [item], "history_added_items": [copy.deepcopy(item)], "candidates": [item], "market_data": {"included": False},
                "configured_sources": [source_id], "source_audit": [{"source_id": source_id, "coverage": "complete", "status": "synthetic-complete", "evidence": {"captured_count": 1}}]}
    history = {"phase": "before_build", "head_sha": "synthetic-sha", "items": []}
    return bundle, metadata, manifest, history


class MailPreviewTests(unittest.TestCase):
    def test_complete_original_email_and_appendix_are_read_only_offline(self):
        inputs = fixture();unchanged = copy.deepcopy(inputs)
        with patch.object(cards, "call_json", side_effect=AssertionError("no API")), patch.object(daily, "update_digest_history", side_effect=AssertionError("no write")):
            report = preview.build(*inputs)
            rendered = preview.render_preview(report, inputs[1]["body"])
        self.assertEqual(inputs, unchanged)
        self.assertEqual(report["appendix_count"], 1)
        self.assertFalse(report["delivery_eligible"])
        self.assertFalse(report["sent"])
        self.assertEqual(report["history_updates"], 0)
        self.assertEqual(report["inline_article_replacements"], 0)
        baseline = daily.markdown_to_email_html(inputs[1]["body"], "unused").split('<main class="email-main">', 1)[1].split('</main>', 1)[0]
        self.assertIn(baseline, rendered)
        self.assertIn("未发送", rendered)
        self.assertIn("原始提醒与正文保持", rendered)

    def test_historical_research_stays_in_appendix_without_freshening_date(self):
        inputs = fixture("2026-03-18T00:00:00+00:00")
        report = preview.build(*inputs)
        row = report["items"][0]
        self.assertEqual(row["date_gate"]["window"], "outside_window")
        self.assertEqual(row["date_gate"]["candidate_gate"], "held")
        self.assertEqual(row["published"], "2026-03-18T00:00:00+00:00")
        self.assertIn("2026-03-18", preview.render_preview(report, inputs[1]["body"]))

    def test_conflicting_date_blocks_candidate_even_with_model_and_editor_pass(self):
        inputs = fixture();mid = inputs[0]["materials"][0]["material_id"]
        inputs[0]["annotations"][mid]["date_status"] = "conflicting"
        row = preview.build(*inputs)["items"][0]
        self.assertEqual(row["status"], preview.COMPLETE)
        self.assertEqual(row["date_gate"]["candidate_gate"], "held")

    def test_research_backfill_and_fixed_source_windows_remain_distinct(self):
        for source_id, expected in (("research|Example|https://example.org/feed", "research_backfill"),
                                    ("fixed_feed|Example|https://example.org/feed", "outside_window")):
            with self.subTest(source_id=source_id):
                inputs = fixture("2026-10-13T00:00:00+00:00", source_id)
                row = preview.build(*inputs)["items"][0]
                self.assertEqual(row["date_gate"]["window"], expected)

    def test_future_naive_and_unmatched_source_dates_are_held(self):
        for value, expected in (("2026-10-21T00:00:00+00:00", "after_cutoff"), ("2026-10-19", "publication_unconfirmed")):
            inputs = fixture(value)
            gate = preview.build(*inputs)["items"][0]["date_gate"]
            self.assertEqual(gate["window"], expected)
            self.assertEqual(gate["candidate_gate"], "held")
        inputs = fixture();inputs[2]["candidates"][0]["published"] = "2026-10-18T00:00:00+00:00"
        gate = preview.build(*inputs)["items"][0]["date_gate"]
        self.assertFalse(gate["snapshot_identity_found"])
        self.assertEqual(gate["candidate_gate"], "held")

    def test_url_and_title_history_duplicates_are_checked_without_writing_history(self):
        for record in ({"url": "https://example.org/study?utm_source=old"}, {"title": "Synthetic portfolio research"}):
            inputs = fixture()
            inputs[3]["items"] = [{"sent_date": "2026-10-19", **record}]
            # Gate inspection uses the same frozen history even when old content has a disclosed audit warning.
            mid = inputs[0]["materials"][0]["material_id"]
            gate = preview.frozen_date_gate(inputs[0]["materials"][0], inputs[0]["annotations"][mid], inputs[2], inputs[3])
            self.assertTrue(gate["history_duplicate"])
            self.assertEqual(gate["candidate_gate"], "held")

    def test_corrupt_saved_body_history_or_market_integrity_refuses_preview(self):
        for which in ("body", "history", "market"):
            inputs = fixture()
            if which == "body":inputs[1]["body"] += "changed"
            elif which == "history":inputs[3]["head_sha"] = "other"
            else:inputs[2]["market_data"] = {"included": True, "us_expected_session": "2026-10-19", "us_strategy_rows": []}
            with self.subTest(which=which), self.assertRaises(ValueError):preview.build(*inputs)

    def test_missing_editor_or_model_rejection_does_not_display_claims(self):
        for rejected in (False, True):
            inputs = fixture();bundle=inputs[0];mid=bundle["materials"][0]["material_id"]
            if rejected:
                bundle["reviews"][mid]["overall"] = "revise"
                bundle["reviews"][mid]["checks"][-1]["verdict"] = "needs_revision"
                bundle["editors"][mid]["review_hash"] = cards.digest(bundle["reviews"][mid])
            else:bundle["editors"][mid] = None
            report = preview.build(*inputs)
            self.assertEqual(report["appendix_count"], 0)
            self.assertNotIn("card", report["items"][0])
            self.assertNotIn(bundle["cards"][mid]["facts"][0]["text_zh"], preview.render_preview(report, inputs[1]["body"]))

    def test_stale_card_editor_and_duplicate_source_do_not_pass(self):
        inputs = fixture();mid = inputs[0]["materials"][0]["material_id"]
        inputs[0]["editors"][mid]["card_hash"] = "stale"
        with self.assertRaises(ValueError):preview.build(*inputs)
        inputs = fixture();inputs[0]["materials"].append(copy.deepcopy(inputs[0]["materials"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate"):preview.build(*inputs)

    def test_pending_material_only_shows_reason_and_safe_original_link(self):
        inputs = fixture();bundle=inputs[0];mid=bundle["materials"][0]["material_id"]
        bundle.update(cards={}, reviews={}, editors={}, pending={mid: "材料不足，待补正文。"})
        report = preview.build(*inputs);rendered=preview.render_preview(report, inputs[1]["body"])
        self.assertEqual(report["pending_count"], 1)
        self.assertIn("材料不足，待补正文", rendered)
        self.assertIn('href="https://example.org/study"', rendered)
        self.assertNotIn("作者报告12%的历史收益", rendered)

    def test_html_escapes_claims_and_unsafe_links_are_rejected(self):
        inputs = fixture();report=preview.build(*inputs)
        report["items"][0]["card"]["title_zh"] = '<script>alert("x")</script>合成标题'
        rendered=preview.render_preview(report, inputs[1]["body"])
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        for url in ("javascript:alert(1)", "https://user:secret@example.org", "https://example.org/\npath"):
            with self.subTest(url=url), self.assertRaises(ValueError):preview.safe_link(url)

    def test_cli_outputs_preview_files_without_sender_metadata_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);bundle,metadata,manifest,history=fixture()
            for name,data in (("bundle",bundle),("metadata",metadata),("manifest",manifest),("history",history)):
                cards.write_json(root/(name+".json"),data)
            args=["--bundle",str(root/"bundle.json"),"--metadata",str(root/"metadata.json"),"--manifest",str(root/"manifest.json"),
                  "--history-before",str(root/"history.json"),"--out-dir",str(root/"preview")]
            self.assertEqual(preview.main(args),0)
            self.assertEqual({p.name for p in (root/"preview").iterdir()}, {"mail-preview-audit.json","mail-preview.html"})
            with self.assertRaisesRegex(ValueError,"new directory"):preview.main(args)


if __name__ == "__main__":
    unittest.main()
