import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import etf_cloud_checkpoint_probe as probe
import etf_content_cards as cards


class CloudCheckpointProbeTests(unittest.TestCase):
    def prepare(self, root):
        source = root / "manifest.json"
        # The real-manifest path is exercised with production data by the cloud job.
        from unittest.mock import patch
        materials = [cards.make_material(title="Synthetic " + str(i), url="https://example.org/" + str(i),
                                        source="synthetic", published="", paragraphs=[probe.TEXT]) for i in range(6)]
        cards.write_json(source, {"synthetic": True})
        metadata = root / "source.json"
        cards.write_json(metadata, {"id": int(probe.SOURCE_RUN)})
        with patch.object(cards, "prepare_manifest", return_value=materials):
            probe.seed(source, root / "seed", metadata)
        return root / "seed"

    def test_process_exit_checkpoint_survives_copy_to_new_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            seed = self.prepare(root)
            copied = root / "restored"
            shutil.copytree(seed, copied)
            probe.restore(copied, root / "proof.json")
            report = cards.read_json(root / "proof.json")
            self.assertTrue(report["real_replay_identical"])
            self.assertEqual(report["provider_network_requests"], 0)
            self.assertEqual(report["synthetic_fault_results"]["completed"]["state"], "parsed")
            self.assertEqual(report["synthetic_fault_results"]["unknown"]["state"], "unknown")
            self.assertFalse(report["delivery_eligible"])

    def test_tampered_or_extra_files_rejected_before_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            seed = self.prepare(root)
            checkpoint = seed / "unknown/run.json"
            original = checkpoint.read_bytes()
            checkpoint.write_bytes(original + b" ")
            with self.assertRaisesRegex(ValueError, "inventory/hash"):
                probe.restore(seed, root / "proof.json")
            checkpoint.write_bytes(original)
            (seed / "extra.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "inventory/hash"):
                probe.restore(seed, root / "proof.json")
            self.assertFalse((root / "proof.json").exists())

    def test_trusted_source_validation_rejects_foreign_failed_and_wrong_workflow(self):
        run = {"id": int(probe.SOURCE_RUN), "repository": {"full_name": "owner/repo"},
               "head_repository": {"full_name": "owner/repo"}, "path": ".github/workflows/etf-allocation-digest.yml",
               "head_branch": "main", "status": "completed", "conclusion": "success", "event": "schedule"}
        probe.verify_source(run, "owner/repo", "main")
        for field, value in (("id", 1), ("path", "other.yml"), ("head_branch", "draft"),
                             ("conclusion", "failure"), ("status", "in_progress"), ("event", "pull_request"),
                             ("head_repository", {"full_name": "foreign/fork"})):
            changed = copy.deepcopy(run)
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                probe.verify_source(changed, "owner/repo", "main")


if __name__ == "__main__":
    unittest.main()
