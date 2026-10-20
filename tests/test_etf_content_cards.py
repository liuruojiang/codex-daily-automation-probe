from __future__ import annotations

import copy
import io
import html
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import etf_content_cards as cards

# Synthetic evidence checks the contract, not investment or translation quality.
TEXT = (
    "The authors report a 12% return in their historical sample before transaction costs were applied. "
    "They explicitly warn that these observations do not establish future performance and that execution costs remain untested."
)


def material(**kwargs):
    return cards.make_material(title="A sample portfolio study", url="https://example.org/study",
                               source="Synthetic fixture", published="2026-10-20", paragraphs=[TEXT], **kwargs)


def response(m):
    return {
        "material_id": m["material_id"], "title_zh": "组合研究的样本结果与成本限制",
        "facts": [{"text_zh": "作者报告，历史样本收益为12%，尚未扣除交易成本。",
                   "citations": [{"paragraph_id": "p1", "quote": TEXT.split(". ")[0] + "."}]}],
        "limitations": [{"text_zh": "作者明确提醒，这些观察不能确立未来表现，执行成本仍未检验。",
                          "citations": [{"paragraph_id": "p1", "quote": TEXT.split(". ")[1]}]}],
        "questions": ["交易成本计入后，研究结果是否仍成立？"],
    }


class ContentCardTests(unittest.TestCase):
    def test_linked_card_is_never_automatically_semantically_verified(self):
        m = material()
        self.assertEqual(cards.validate_card(m, response(m)), [])
        out = cards.replay([m], {m["material_id"]: response(m)})
        self.assertEqual(out["mode"], "shadow_only")
        self.assertEqual(out["semantic_review"], "pending")
        self.assertEqual(out["items"][0]["status"], "evidence_linked_pending_review")

    def test_same_numbers_and_exact_quote_do_not_prove_semantics(self):
        m = material()
        r = response(m)
        r["facts"][0]["text_zh"] = "作者保证未来收益为12%。"  # Intentionally wrong.
        out = cards.replay([m], {m["material_id"]: r})
        self.assertEqual(out["semantic_review"], "pending")
        self.assertNotIn("verified", out["items"][0])

    def test_material_changes_invalidate_response_and_identity(self):
        m = material()
        changed = cards.make_material(title=m["title"], url=m["url"], source=m["source"], published=m["published"], paragraphs=[TEXT.replace("12%", "13%")])
        self.assertIn("stale_or_wrong_material", cards.validate_card(changed, response(m)))
        forged = copy.deepcopy(m)
        forged["paragraphs"][0]["text"] += " changed"
        with self.assertRaisesRegex(ValueError, "identity"):
            cards.validate_card(forged, response(m))

    def test_wrong_article_quote_and_invented_numbers_are_rejected(self):
        m = material()
        for modify, expected in (
            (lambda r: r["facts"][0]["citations"][0].update(quote="This quotation belongs to a completely different paper."), "quote_not_in_material"),
            (lambda r: r["facts"][0].update(text_zh="作者报告历史收益为99%。"), "unsupported_claim_number"),
            (lambda r: r["facts"][0].update(text_zh="作者报告历史收益为-12%。"), "unsupported_claim_number"),
            (lambda r: r["facts"][0].update(citations=[]), "missing_citations"),
            (lambda r: r.update(title_zh="收益达到99%的组合"), "unsupported_title_number"),
        ):
            with self.subTest(expected=expected):
                r = response(m)
                modify(r)
                self.assertIn(expected, cards.validate_card(m, r))

    def test_malformed_model_fields_fail_closed(self):
        m = material()
        for mutation in (
            lambda r: r.update(facts="not a list"),
            lambda r: r.update(questions=None),
            lambda r: r.update(extra="unexpected"),
            lambda r: r["facts"][0]["citations"][0].update(paragraph_id=[]),
            lambda r: r["facts"][0].update(text_zh=None),
            lambda r: r.update(facts=[]),
        ):
            r = response(m)
            mutation(r)
            self.assertTrue(cards.validate_card(m, r))
        for r in (None, [], "bad"):
            self.assertEqual(cards.validate_card(m, r), ["invalid_schema"])

    def test_title_only_aggregates_and_paywalls_cannot_generate_facts(self):
        for level in ("title_only", "aggregate_listing"):
            m = material(evidence_level=level)
            self.assertFalse(cards.ready(m))
        m = cards.make_material(title="Restricted research", url="https://example.org/paywall", source="fixture", published="", paragraphs=["Subscribe now to unlock this article. Log in to continue reading the full text."])
        self.assertFalse(cards.ready(m))

    def test_legacy_manifest_is_not_promoted_to_full_text(self):
        row = {"source": "Fixture", "title": "Study", "url": "https://example.org/paper", "summary": TEXT, "evidence_level": "full_text"}
        prepared = cards.prepare_manifest({"candidates": [row, row]})
        self.assertEqual(len(prepared), 1)
        self.assertEqual(prepared[0]["evidence_level"], "captured_excerpt")
        self.assertEqual(prepared[0]["paragraphs"][0]["text"], TEXT)
        row["is_aggregator"] = True
        self.assertEqual(cards.prepare_manifest({"candidates": [row]})[0]["evidence_level"], "aggregate_listing")

    def test_material_and_card_identity_bind_scope_and_all_fields(self):
        m = material()
        scoped = material(evidence_level="publisher_abstract")
        self.assertNotEqual(m["material_id"], scoped["material_id"])
        r = response(m)
        before = cards.digest(r)
        r["questions"].append("样本选择是否存在偏差？")
        self.assertNotEqual(before, cards.digest(r))

    def test_enriched_rendered_material_is_not_lost_or_called_full_text(self):
        raw = {"source": "fixture", "title": "Study", "url": "https://example.org/study", "summary": "Short listing"}
        enriched = {**raw, "summary": TEXT}
        materials = cards.prepare_manifest({"candidates": [raw], "selected_items": [enriched]})
        self.assertEqual(len(materials), 2)
        self.assertEqual([cards.ready(m) for m in materials], [False, True])
        self.assertEqual({m["evidence_level"] for m in materials}, {"captured_excerpt"})

    def test_missing_duplicate_and_extra_responses_are_accounted_for(self):
        m = material()
        self.assertEqual(cards.replay([m], {})["items"][0]["status"], "awaiting_model")
        with self.assertRaisesRegex(ValueError, "unknown"):
            cards.replay([m], {"unknown": response(m)})
        with self.assertRaisesRegex(ValueError, "duplicate"):
            cards.replay([m, m], {})

    def test_html_escapes_untrusted_text_and_unsafe_urls_are_refused(self):
        m = material()
        r = response(m)
        r["questions"] = ["核验<script>alert(1)</script>是否为注入？"]
        text = cards.render_html(cards.replay([m], {m["material_id"]: r}))
        self.assertNotIn("<script>", text)
        self.assertIn("&lt;script&gt;", text)
        with self.assertRaises(ValueError):
            cards.make_material(title="Unsafe", url="javascript:alert(1)", source="", published="", paragraphs=[])

    def test_cached_response_never_repeats_request(self):
        m = material()
        raw = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(response(m))}}], "usage": {"total_tokens": 120}}
        called = []
        def transport(req, timeout):
            called.append(req)
            return io.BytesIO(json.dumps(raw).encode())
        with tempfile.TemporaryDirectory() as temp:
            opts = dict(endpoint="https://example.org/v1/chat/completions", model="configured-model", api_key="secret-test-value", max_output_tokens=500, transport=transport)
            self.assertEqual(cards.call_model(m, Path(temp), **opts), raw)
            self.assertEqual(cards.call_model(m, Path(temp), **opts), raw)
            self.assertEqual(len(called), 1)
            text = next(Path(temp).glob("*.json")).read_text(encoding="utf-8")
            self.assertNotIn("secret-test-value", text)
            self.assertEqual(cards.model_card(raw), response(m))

    def test_timeout_unknown_is_not_retried_and_errors_are_sanitized(self):
        def fail(*args, **kwargs):
            raise TimeoutError("secret-test-value")
        with tempfile.TemporaryDirectory() as temp:
            opts = dict(endpoint="https://example.org/v1/chat/completions", model="model", api_key="secret-test-value", max_output_tokens=500, transport=fail)
            with self.assertRaisesRegex(RuntimeError, "unknown") as err:
                cards.call_model(material(), Path(temp), **opts)
            self.assertNotIn("secret-test-value", str(err.exception))
            with self.assertRaisesRegex(RuntimeError, "inspect receipt"):
                cards.call_model(material(), Path(temp), **opts)

    def test_incomplete_response_not_accepted(self):
        with self.assertRaisesRegex(ValueError, "incomplete"):
            cards.model_card({"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]})

    def test_generate_limits_calls_and_accounts_for_unprocessed_material(self):
        one = material()
        two = cards.make_material(title="Second portfolio study", url="https://example.org/second", source="fixture", published="", paragraphs=[TEXT])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cards.write_json(root / "input.json", [one, two])
            def fake(m, *args, **kwargs):
                return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(response(m))}}]}
            config = {"ETF_CARD_API_URL": "https://example.org/chat/completions", "ETF_CARD_MODEL": "test-model", "ETF_CARD_API_KEY": "test-secret"}
            with patch.dict(cards.os.environ, config), patch.object(cards, "call_model", side_effect=fake) as call:
                self.assertEqual(cards.main(["generate", "--materials", str(root / "input.json"), "--out-dir", str(root / "out"), "--max-calls", "1"]), 0)
                self.assertEqual(call.call_count, 1)
            report = cards.read_json(root / "out/cards.json")
            self.assertEqual([i["status"] for i in report["items"]], ["evidence_linked_pending_review", "awaiting_model"])

    def test_oversized_material_is_rejected_before_request(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cards.write_json(root / "input.json", [material()])
            config = {"ETF_CARD_API_URL": "https://example.org/chat/completions", "ETF_CARD_MODEL": "test-model", "ETF_CARD_API_KEY": "test-secret"}
            with patch.dict(cards.os.environ, config), patch.object(cards, "call_model") as call:
                self.assertEqual(cards.main(["generate", "--materials", str(root / "input.json"), "--out-dir", str(root / "out"), "--max-input-chars", "100"]), 1)
                call.assert_not_called()
                row = cards.read_json(root / "out/cards.json")["items"][0]
                self.assertEqual(row["status"], "generation_failed")
                self.assertEqual(row["errors"], ["InputLimitExceeded"])

    def test_insecure_model_endpoint_is_refused_before_reservation(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "HTTPS"):
                cards.call_model(material(), Path(temp), endpoint="http://example.org/chat", model="model", api_key="secret", max_output_tokens=500)
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_evidence_ids_copy_exact_source_without_model_quoting(self):
        m = material()
        units = cards.evidence_units(m)
        self.assertEqual(len(units), 2)
        for unit in units:
            self.assertIn(unit["text"], TEXT)
        card = {**response(m), "facts": [{"text_zh": "作者报告，历史样本收益为12%，尚未扣除交易成本。", "evidence_ids": [units[0]["id"]]}], "limitations": []}
        expanded = cards.expand_evidence_ids(m, card)
        self.assertEqual(cards.validate_card(m, expanded), [])
        self.assertEqual(expanded["facts"][0]["citations"][0]["quote"], units[0]["text"])
        card["facts"][0]["evidence_ids"] = ["p1.s9999"]
        with self.assertRaisesRegex(ValueError, "invalid evidence id"):
            cards.expand_evidence_ids(m, card)

    def test_latex_percent_normalization_does_not_allow_numeric_or_unit_changes(self):
        self.assertEqual(cards.numbers(r"95\\%"), cards.numbers("95%"))
        self.assertNotEqual(cards.numbers("95%"), cards.numbers("95bp"))
        self.assertNotEqual(cards.numbers("$499.8 million"), cards.numbers("4.998亿"))

    def test_generation_resumes_next_article_without_explicit_responses_flag(self):
        one = material()
        two = cards.make_material(title="Second study", url="https://example.org/two", source="fixture", published="", paragraphs=[TEXT])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cards.write_json(root / "input.json", [one, two])
            args = ["generate", "--materials", str(root / "input.json"), "--out-dir", str(root / "out"), "--max-calls", "1"]
            config = {"ETF_CARD_API_URL": "https://example.org/chat", "ETF_CARD_MODEL": "model", "ETF_CARD_API_KEY": "secret"}
            def fake(m, *a, **kw):
                return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(response(m))}}]}
            with patch.dict(cards.os.environ, config), patch.object(cards, "call_model", side_effect=fake) as call:
                self.assertEqual(cards.main(args), 0)
                self.assertEqual(cards.main(args), 0)
                self.assertEqual([x.args[0]["material_id"] for x in call.call_args_list], [one["material_id"], two["material_id"]])
                self.assertEqual(cards.main(args), 0)
                self.assertEqual(call.call_count, 2)
                with patch("sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit):
                    cards.main(args + ["--thinking", "disabled"])
                self.assertEqual(call.call_count, 2)

    def test_generation_failure_is_visible_and_other_articles_continue(self):
        one = material()
        two = cards.make_material(title="Second study", url="https://example.org/two", source="fixture", published="", paragraphs=[TEXT])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cards.write_json(root / "input.json", [one, two])
            args = ["generate", "--materials", str(root / "input.json"), "--out-dir", str(root / "out"), "--max-calls", "2"]
            config = {"ETF_CARD_API_URL": "https://example.org/chat", "ETF_CARD_MODEL": "model", "ETF_CARD_API_KEY": "secret"}
            good = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(response(two))}}]}
            with patch.dict(cards.os.environ, config), patch.object(cards, "call_model", side_effect=[RuntimeError("private error"), good]) as call:
                self.assertEqual(cards.main(args), 1)
                report = cards.read_json(root / "out/cards.json")
                self.assertEqual([i["status"] for i in report["items"]], ["generation_failed", "evidence_linked_pending_review"])
                self.assertNotIn("private error", json.dumps(report))
                self.assertEqual(cards.main(args), 1)
                self.assertEqual(call.call_count, 2)

    def test_thinking_mode_is_explicit_and_quoted_preview_is_bounded_per_source(self):
        m = material()
        self.assertNotIn("thinking", cards.request_payload(m, "model"))
        self.assertEqual(cards.request_payload(m, "model", thinking="disabled")["thinking"], {"type": "disabled"})
        report = cards.replay([m], {m["material_id"]: response(m)})
        report["items"].append(report["items"][0])
        text = cards.render_html(report)
        quotations = re.findall(r"<blockquote>(.*?)</blockquote>", text)
        self.assertLessEqual(sum(len(html.unescape(q).split()) for q in quotations), 25)

    def test_cli_prepare_and_replay_have_no_production_side_effects(self):
        m = material()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "materials.json"
            replies = root / "replies.json"
            source.write_text(json.dumps([m]), encoding="utf-8")
            replies.write_text(json.dumps({m["material_id"]: response(m)}), encoding="utf-8")
            with patch.object(cards.dr, "fetch_bytes", side_effect=AssertionError("replay must be offline")):
                self.assertEqual(cards.main(["replay", "--materials", str(source), "--responses", str(replies), "--out-dir", str(root / "artifacts")]), 0)
            self.assertTrue((root / "artifacts/preview.html").exists())
            self.assertFalse((root / "digest_history").exists())
            self.assertFalse((root / "artifacts/metadata.json").exists())


if __name__ == "__main__":
    unittest.main()
