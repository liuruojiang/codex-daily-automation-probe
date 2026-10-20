import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import etf_content_cards as cards
import etf_shadow_run as shadow

TEXT = ("The study reports a 12% historical return before transaction costs and uses a limited sample of portfolio observations. "
        "The authors explain that these observations cannot establish future performance and that implementation costs have not been tested.")


def materials():
    return [cards.make_material(title="Portfolio study " + str(i), url="https://example.org/research/" + str(i),
                                source="synthetic", published="", paragraphs=[TEXT]) for i in range(2)]


def config(**changes):
    return {"run_label": "synthetic-day", "endpoint": "https://example.org/v1/chat/completions", "generator": "generator",
            "reviewer": "reviewer", "thinking": None, "max_requests": 4, "max_reserved_output_tokens": 11200,
            "generation_tokens": 2400, "review_tokens": 3200, "max_input_chars": 60000, **changes}


def transport(calls, change=None):
    def send(request, timeout):
        payload = json.loads(request.data)
        calls.append(payload["model"])
        packet = json.loads(payload["messages"][1]["content"])
        if payload["model"] == "generator":
            result = {"material_id": packet["material_id"], "title_zh": "组合研究历史表现",
                      "facts": [{"text_zh": "作者报告历史收益为12%，未扣交易成本。", "evidence_ids": ["p1.s1"]}],
                      "limitations": [], "questions": []}
        else:
            result = {"material_id": packet["material_id"], "card_hash": packet["card_hash"], "overall": "pass",
                      "checks": [{"claim_id": cid, "verdict": "supported", "reason_zh": "与原文保持一致。",
                                  "evidence_ids": ["p1.s1"] if cid.startswith("facts.") else []} for cid in packet["required_claim_ids"]]}
        if change:
            result = change(result, payload)
        raw = {"model": payload["model"], "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}],
               "usage": {"total_tokens": 100}}
        return io.BytesIO(json.dumps(raw).encode())
    return send


class ShadowTests(unittest.TestCase):
    def run_batch(self, folder, calls, *, conf=None, live=True, maximum=10, send=None, ms=None):
        return shadow.advance(ms or materials(), conf or config(), "sourcehash", Path(folder), live=live,
                              max_new_requests=maximum, api_key="synthetic-secret", transport=send or transport(calls))

    def test_cumulative_requests_survive_multiple_invocations(self):
        calls = []
        with tempfile.TemporaryDirectory() as p:
            conf = config(max_requests=3)
            for _ in range(5):
                report = self.run_batch(p, calls, conf=conf, maximum=1)
            self.assertEqual(calls, ["generator", "reviewer", "generator"])
            self.assertEqual(report["budget"]["requests_reserved"], 3)
            self.assertEqual([r["status"] for r in report["items"]], ["model_review_passed", "awaiting_semantic_review"])
            self.assertEqual(report["editorial_review"], "pending")

    def test_output_reservation_is_cumulative_not_a_dollar_cap(self):
        calls = []
        with tempfile.TemporaryDirectory() as p:
            conf = config(max_reserved_output_tokens=3000)
            self.run_batch(p, calls, conf=conf)
            report = self.run_batch(p, calls, conf=conf)
            self.assertEqual(calls, ["generator"])
            self.assertEqual(report["budget"]["output_tokens_reserved"], 2400)
            self.assertEqual(report["budget"]["monetary_cap"], "not_established")

    def test_plan_and_replay_never_call_network(self):
        calls = []
        with tempfile.TemporaryDirectory() as p:
            self.run_batch(p, calls, live=False)
            report = self.run_batch(p, calls, live=False)
            self.assertEqual(calls, [])
            self.assertEqual(report["budget"]["requests_reserved"], 0)
            self.assertTrue(all(r["status"] == "awaiting_generation" for r in report["items"]))

    def test_unknown_failure_does_not_retry_and_other_article_continues(self):
        calls = []
        sender = transport(calls)
        def fail_first(req, timeout):
            if not calls:
                calls.append("unknown")
                raise OSError("synthetic-secret and provider body")
            return sender(req, timeout)
        with tempfile.TemporaryDirectory() as p:
            report = self.run_batch(p, calls, send=fail_first)
            self.run_batch(p, calls)
            self.assertEqual(calls, ["unknown", "generator", "reviewer"])
            self.assertEqual(report["budget"]["unknown_outcomes"], 1)
            self.assertEqual(report["items"][1]["status"], "model_review_passed")
            for file in Path(p).rglob("*.json"):
                self.assertNotIn("synthetic-secret", file.read_text(encoding="utf-8"))

    def test_completed_receipt_recovers_crash_without_another_request(self):
        calls = []
        with tempfile.TemporaryDirectory() as p:
            with patch.object(shadow, "accept_response", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):self.run_batch(p, calls, maximum=1)
            report = self.run_batch(p, calls, live=False)
            self.assertEqual(calls, ["generator"])
            self.assertEqual(report["items"][0]["status"], "awaiting_semantic_review")
            self.assertEqual(report["budget"]["requests_reserved"], 1)
            self.run_batch(p, calls, maximum=1)
            self.assertEqual(calls, ["generator", "reviewer"])

    def test_reserved_without_receipt_is_unknown_and_never_retried(self):
        calls = []
        with tempfile.TemporaryDirectory() as p:
            with patch.object(cards, "call_json", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):self.run_batch(p, calls)
            report = self.run_batch(p, calls, live=False)
            self.assertEqual(report["budget"]["unknown_outcomes"], 1)
            self.assertEqual(report["budget"]["usage_missing_requests"], 1)
            self.run_batch(p, calls, maximum=1)
            self.assertEqual(calls, ["generator"])
            journal = cards.read_json(Path(p)/"run.json")
            self.assertEqual(journal["attempts"]["generate:" + materials()[0]["material_id"]]["state"], "unknown")

    def test_oversize_input_is_not_clipped_or_charged(self):
        calls = []
        with tempfile.TemporaryDirectory() as p:
            report = self.run_batch(p, calls, conf=config(max_input_chars=1))
            self.assertEqual(calls, [])
            self.assertEqual(report["budget"]["requests_reserved"], 0)
            self.assertTrue(all(r["errors"] == ["InputLimitExceeded"] for r in report["items"]))

    def test_corrupt_receipt_does_not_reissue_request_or_block_other_articles(self):
        calls = []
        with tempfile.TemporaryDirectory() as p:
            with patch.object(cards, "call_json", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):self.run_batch(p, calls)
            journal = cards.read_json(Path(p)/"run.json")
            key = next(iter(journal["attempts"].values()))["request_key"]
            receipts = Path(p)/"receipts";receipts.mkdir()
            (receipts/(key + ".json")).write_text("{broken", encoding="utf-8")
            report = self.run_batch(p, calls)
            self.assertEqual(calls, ["generator", "reviewer"])
            self.assertEqual(report["budget"]["unknown_outcomes"], 1)
            self.assertEqual(report["items"][1]["status"], "model_review_passed")

    def test_bad_numeric_card_is_not_sent_to_reviewer(self):
        calls = []
        def change(result, payload):
            result["facts"][0]["text_zh"] = "作者报告历史收益为99%。"
            return result
        with tempfile.TemporaryDirectory() as p:
            report = self.run_batch(p, calls, send=transport(calls, change))
            self.assertEqual(calls, ["generator", "generator"])
            self.assertTrue(all(r["status"] == "blocked_by_evidence" for r in report["items"]))

    def test_model_rejection_and_invalid_review_are_not_approved(self):
        for invalid in (False, True):
            calls = []
            def change(result, payload):
                if payload["model"] == "reviewer":
                    if invalid:result["checks"].pop(0)
                    else:
                        result["overall"] = "revise"
                        result["checks"][1]["verdict"] = "needs_revision"
                return result
            with tempfile.TemporaryDirectory() as p:
                report = self.run_batch(p, calls, send=transport(calls, change))
                self.assertTrue(all(r["status"] == ("review_invalid" if invalid else "semantic_revision_required") for r in report["items"]))

    def test_malformed_card_is_not_regenerated_on_resume(self):
        calls = []
        with tempfile.TemporaryDirectory() as p:
            self.run_batch(p, calls, send=transport(calls, lambda result, payload: "not a card"))
            report = self.run_batch(p, calls)
            self.assertEqual(calls, ["generator", "generator"])
            self.assertTrue(all(r["status"] == "generation_failed" for r in report["items"]))

    def test_changed_budget_source_or_prompt_requires_new_directory(self):
        calls = []
        with tempfile.TemporaryDirectory() as p:
            self.run_batch(p, calls, maximum=1)
            with self.assertRaisesRegex(ValueError, "context changed"):
                self.run_batch(p, calls, conf=config(max_requests=20))
            with patch.object(cards, "SYSTEM_PROMPT", cards.SYSTEM_PROMPT + "changed"):
                with self.assertRaisesRegex(ValueError, "context changed"):self.run_batch(p, calls)
            changed = materials(); changed.reverse()
            with self.assertRaisesRegex(ValueError, "context changed"):self.run_batch(p, calls, ms=changed)
            self.assertEqual(calls, ["generator"])

    def test_invalid_or_credential_bearing_config_rejected_before_persistence(self):
        calls = []
        with tempfile.TemporaryDirectory() as p:
            for conf in (config(endpoint="https://example.org/?key=secret"), config(max_requests=-1), config(api_key="secret")):
                with self.assertRaises(ValueError):self.run_batch(p, calls, conf=conf)
            self.assertFalse((Path(p)/"run.json").exists())

    def test_wrong_cached_card_does_not_reuse_review(self):
        calls = []
        with tempfile.TemporaryDirectory() as p:
            self.run_batch(p, calls)
            checkpoint = Path(p)/"run.json";journal = cards.read_json(checkpoint)
            journal["attempts"]["generate:" + materials()[0]["material_id"]]["output"]["title_zh"] = "新卡片标题"
            cards.write_json(checkpoint, journal)
            with self.assertRaisesRegex(ValueError, "cached request"):
                self.run_batch(p, calls)
            self.assertEqual(len(calls), 4)

    def test_operating_system_lock_prevents_concurrent_writers(self):
        with tempfile.TemporaryDirectory() as p:
            with shadow.run_lock(Path(p)):
                with self.assertRaises(RuntimeError):
                    with shadow.run_lock(Path(p)):pass
            with shadow.run_lock(Path(p)):pass

    def test_duplicate_urls_use_richer_capture_without_promoting_level(self):
        first = materials()[0]
        richer = cards.make_material(**{k:first[k] for k in ("title", "url", "source", "published")}, paragraphs=[TEXT + " More source context."])
        selected = shadow.select_materials([first, richer], 6)
        self.assertEqual(selected, [richer])
        self.assertEqual(selected[0]["evidence_level"], "captured_excerpt")

    def test_title_only_material_does_not_consume_budget(self):
        calls = []
        m = cards.make_material(title="Short title", url="https://example.org/thin", source="synthetic", published="", paragraphs=[], evidence_level="title_only")
        with tempfile.TemporaryDirectory() as p:
            report = self.run_batch(p, calls, ms=[m])
            self.assertEqual(calls, [])
            self.assertEqual(report["items"][0]["status"], "insufficient_material")

    def test_incomplete_usage_is_reported_as_unknown_without_breaking_replay(self):
        calls = []
        with tempfile.TemporaryDirectory() as p:
            self.run_batch(p, calls, maximum=1)
            checkpoint = Path(p)/"run.json";journal = cards.read_json(checkpoint)
            next(iter(journal["attempts"].values()))["usage"] = {"total_tokens": "invalid"}
            cards.write_json(checkpoint, journal)
            report = self.run_batch(p, calls, live=False)
            self.assertEqual(report["budget"]["usage_missing_requests"], 1)
            self.assertEqual(report["budget"]["provider_reported_total_tokens"], 0)

    def test_preexisting_output_without_checkpoint_is_not_overwritten(self):
        calls = []
        with tempfile.TemporaryDirectory() as p:
            cards.write_json(Path(p)/"responses.json", {"prior": "unbound"})
            with self.assertRaisesRegex(ValueError, "no checkpoint"):self.run_batch(p, calls)
            self.assertEqual(cards.read_json(Path(p)/"responses.json"), {"prior": "unbound"})
            self.assertEqual(calls, [])

    def test_cloud_workflow_is_read_only_offline_and_archives_failure_state(self):
        path = Path(__file__).resolve().parents[1]/".github/workflows/etf-shadow-digest.yml"
        text = path.read_text(encoding="utf-8")
        workflow = yaml.load(text, Loader=yaml.BaseLoader)
        self.assertEqual(workflow["permissions"], {"contents": "read", "actions": "read"})
        self.assertIn("workflow_run", workflow["on"])
        self.assertNotIn("secrets.", text)
        self.assertNotIn("send_report.py", text)
        self.assertNotIn("git push", text)
        steps = workflow["jobs"]["shadow"]["steps"]
        execute = next(s for s in steps if s.get("name", "").startswith("Plan or replay"))
        self.assertIn("shadow.main(['plan'", execute["run"])
        self.assertIn("'--max-new-requests', '0'", execute["run"])
        upload = steps[-1]
        self.assertEqual(upload["if"], "always()")
        self.assertEqual(upload["with"]["retention-days"], "30")


if __name__ == "__main__":
    unittest.main()
