import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from validate_etf_delivery import validate


class DeliveryGateTests(unittest.TestCase):
    def setUp(self):
        self.url = "https://example.com/research?a=1&b=2"
        self.metadata = {"body": f"# 完整日报\n\n- 链接：{self.url}\n\n**事实层**：来源事实。\n", "attachment": None, "html_body": '<html><body><a href="https://example.com/research?a=1&amp;b=2">完整文章</a></body></html>'}
        self.manifest = {"body_sha256": hashlib.sha256(self.metadata["body"].encode()).hexdigest(), "selected_items": [{"url": self.url}], "history_added_items": [{"url": self.url}]}

    def test_valid_full_email_passes(self):
        self.assertEqual(validate(self.metadata, self.manifest), [])

    def test_edited_body_after_collection_is_blocked(self):
        self.metadata["body"] += "被改动的报告"
        self.assertTrue(any("body_sha256" in e for e in validate(self.metadata, self.manifest)))

    def test_selection_hidden_by_section_cap_is_blocked(self):
        self.manifest["selected_items"].append({"url": "https://example.com/fifth-research"})
        self.assertTrue(any("selected_items differs" in e for e in validate(self.metadata, self.manifest)))

    def test_unrendered_history_addition_is_blocked(self):
        self.manifest["history_added_items"].append({"url": "https://example.com/hidden"})
        self.assertTrue(any("history_added_items differs" in e for e in validate(self.metadata, self.manifest)))

    def test_rendered_item_missing_from_history_is_blocked(self):
        self.manifest["history_added_items"] = []
        self.assertTrue(validate(self.metadata, self.manifest))

    def test_html_text_url_is_not_a_clickable_link(self):
        self.metadata["html_body"] = f"<p>{self.url}</p>"
        self.assertTrue(any("clickable" in e for e in validate(self.metadata, self.manifest)))

    def test_attachments_and_missing_attachment_declaration_are_blocked(self):
        self.metadata["attachment"] = "report.md"
        self.assertTrue(validate(self.metadata, self.manifest))
        del self.metadata["attachment"]
        self.assertTrue(validate(self.metadata, self.manifest))

    def test_duplicate_rendering_is_blocked(self):
        self.metadata["body"] += f"- 链接：{self.url}\n"
        self.manifest["body_sha256"] = hashlib.sha256(self.metadata["body"].encode()).hexdigest()
        self.assertTrue(any("more than once" in e for e in validate(self.metadata, self.manifest)))

    def test_source_audit_link_is_not_counted_as_rendered_item(self):
        self.metadata["body"] += "| 未确认来源 | https://example.com/another |\n"
        self.manifest["body_sha256"] = hashlib.sha256(self.metadata["body"].encode()).hexdigest()
        self.assertEqual(validate(self.metadata, self.manifest), [])

    def test_cli_failure_really_returns_nonzero_before_send(self):
        # The CLI requires the new independent ledger, not just rendered URLs.
        source_id = "research|fixture|https://example.com/feed"
        self.manifest.update(schema_version=2, decision_policy="etf-candidates-v1",
                             head_sha="fixture", cutoff_utc="2026-09-07T21:00:00Z",
                             configured_sources=[source_id],
                             source_audit=[{"source_id": source_id, "coverage": "complete", "evidence": {"captured_count": 1}}],
                             candidates=[{"source_id": source_id, "url": self.url, "title": "Portfolio methodology",
                                          "published": "2026-09-07T12:00:00Z", "pipeline_decision": {"reason": "rendered"},
                                          "summary": "This study examines how different portfolio construction methods respond to changes in the underlying investment opportunity set. We compare the resulting allocations across multiple evaluation periods and document where the conclusions depend on assumptions about implementation and information availability."}])
        with tempfile.TemporaryDirectory() as directory:
            metadata_path = Path(directory) / "metadata.json"
            manifest_path = Path(directory) / "collection_manifest.json"
            metadata_path.write_text(json.dumps(self.metadata), encoding="utf-8")
            manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
            (Path(directory) / "history_before.json").write_text(json.dumps({"phase": "before_build", "head_sha": "fixture", "items": []}), encoding="utf-8")
            command = [sys.executable, str(ROOT / "scripts/validate_etf_delivery.py"), str(metadata_path)]
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)
            self.manifest["candidates"] = []
            manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            manifest_path.unlink()
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stdout)["status"], "FAILED")


class WorkflowDeliveryGateTests(unittest.TestCase):
    def test_no_send_dispatch_keeps_build_validation_and_artifact(self):
        workflow = (ROOT / ".github/workflows/etf-allocation-digest.yml").read_text(encoding="utf-8")
        self.assertIn("send_email:\n        description:", workflow)
        self.assertIn("type: boolean\n        default: true", workflow)
        self.assertLess(workflow.index("name: Build US ETF"), workflow.index("name: Validate ETF delivery artifacts"))
        self.assertLess(workflow.index("name: Validate ETF delivery artifacts"), workflow.index("name: Send Gmail"))
        guard = "if: ${{ github.event_name != 'workflow_dispatch' || inputs.send_email != false }}"
        for step in ("Send Gmail", "Persist digest history"):
            block = workflow.split(f"- name: {step}", 1)[1].split("\n      - ", 1)[0]
            self.assertIn(guard, block)
        validation = workflow.split("- name: Validate ETF delivery artifacts", 1)[1].split("\n      - ", 1)[0]
        self.assertNotIn("if:", validation)
        self.assertIn("python scripts/validate_etf_delivery.py artifacts/metadata.json", validation)
        self.assertIn("if: always()", workflow)


if __name__ == "__main__":
    unittest.main()
