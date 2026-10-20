import copy
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import etf_cloud_checkpoint_probe as probe
import etf_content_cards as cards


class CloudCheckpointProbeTests(unittest.TestCase):
    def producer(self):
        run = {"id": int(probe.RESUME_ORIGIN["GITHUB_RUN_ID"]), "run_attempt": 1,
               "head_sha": probe.RESUME_HEAD, "repository": {"full_name": probe.RESUME_REPOSITORY, "id": 123},
               "head_repository": {"full_name": probe.RESUME_REPOSITORY}, "path": ".github/workflows/delivery-regression.yml",
               "head_branch": "codex/etf-evidence-cards-step1", "event": "pull_request", "status": "completed",
               "conclusion": "success", "pull_requests": [{"number": 158}]}
        artifact = {**probe.RESUME_ARTIFACT, "expired": False,
                    "workflow_run": {"id": run["id"], "head_sha": probe.RESUME_HEAD, "repository_id": 123, "head_repository_id": 123}}
        commit = {"sha": probe.RESUME_ORIGIN["GITHUB_SHA"], "parents": [{"sha": probe.RESUME_HEAD}, {"sha": probe.RESUME_BASE}]}
        return run, artifact, commit

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

    def test_cross_run_restore_preserves_original_origin_and_requires_explicit_verification(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.dict(os.environ, probe.RESUME_ORIGIN):
                seed = self.prepare(root)
            copied = root / "consumer"
            shutil.copytree(seed, copied)
            with patch.dict(os.environ, {"GITHUB_RUN_ID": "999999", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_SHA": "consumer-sha"}):
                with self.assertRaisesRegex(ValueError, "provenance mismatch"):
                    probe.restore(copied, root / "proof.json")
                run, artifact, commit = self.producer()
                verified = probe.verify_resume(run, artifact, commit, probe.RESUME_REPOSITORY)
                stale = copy.deepcopy(verified)
                stale["consumer_origin"]["GITHUB_RUN_ID"] = "other"
                with self.assertRaisesRegex(ValueError, "metadata mismatch"):
                    probe.restore(copied, root / "proof.json", stale)
                probe.restore(copied, root / "proof.json", verified)
                report = cards.read_json(root / "proof.json")
                self.assertEqual(report["checkpoint_origin"], probe.RESUME_ORIGIN)
                self.assertEqual(report["origin"]["GITHUB_RUN_ID"], "999999")
                self.assertIn("cross-workflow-run", report["scope"])
                self.assertEqual(report["provider_network_requests"], 0)

    def test_cross_run_producer_validation_rejects_wrong_run_versions_status_and_artifact(self):
        with patch.dict(os.environ, {"GITHUB_RUN_ID": "999999"}):
            run, artifact, commit = self.producer()
            probe.verify_resume(run, artifact, commit, probe.RESUME_REPOSITORY)
            mutations = [(0, "id", 1), (0, "run_attempt", 2), (0, "head_sha", "changed"),
                         (0, "repository", {"full_name": "foreign/repo", "id": 123}),
                         (0, "head_repository", {"full_name": "foreign/fork"}), (0, "path", "other.yml"),
                         (0, "head_branch", "main"), (0, "event", "workflow_dispatch"),
                         (0, "status", "in_progress"), (0, "conclusion", "failure"), (0, "pull_requests", []),
                         (1, "id", 1), (1, "name", "similar-name"), (1, "digest", "sha256:changed"),
                         (1, "expired", True), (1, "workflow_run", {"id": 1}),
                         (2, "sha", "other"), (2, "parents", [{"sha": probe.RESUME_HEAD}])]
            for index, field, value in mutations:
                changed = copy.deepcopy([run, artifact, commit])
                changed[index][field] = value
                with self.subTest(index=index, field=field), self.assertRaises(ValueError):
                    probe.verify_resume(*changed, probe.RESUME_REPOSITORY)
            with self.assertRaises(ValueError):
                probe.verify_resume(run, artifact, commit, "foreign/repo")
        for run_id in (probe.RESUME_ORIGIN["GITHUB_RUN_ID"], "local", "0"):
            with patch.dict(os.environ, {"GITHUB_RUN_ID": run_id}), self.assertRaises(ValueError):
                probe.verify_resume(*self.producer(), probe.RESUME_REPOSITORY)

    def test_inventory_counts_nested_bundle_and_missing_receipt_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            seed = self.prepare(root)
            nested = seed / "completed/bundle.json"
            nested.write_text("{}")
            with self.assertRaisesRegex(ValueError, "inventory/hash"):
                probe.restore(seed, root / "proof.json")
            nested.unlink()
            next((seed / "completed/receipts").glob("*.json")).unlink()
            with self.assertRaisesRegex(ValueError, "inventory/hash"):
                probe.restore(seed, root / "proof.json")

    def test_cross_run_workflow_has_pinned_download_after_source_verification(self):
        path = Path(__file__).resolve().parents[1] / ".github/workflows/delivery-regression.yml"
        workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        job = workflow["jobs"]["etf-checkpoint-cross-run-restore"]
        self.assertEqual(job["permissions"], {"contents": "read", "actions": "read"})
        self.assertIn("head.repo.full_name == github.repository", job["if"])
        steps = job["steps"]
        verify = next(i for i, step in enumerate(steps) if "verify-resume" in step.get("run", ""))
        download = next(i for i, step in enumerate(steps) if "download-artifact" in step.get("uses", ""))
        self.assertLess(verify, download)
        self.assertEqual(steps[download]["with"]["artifact-ids"], str(probe.RESUME_ARTIFACT["id"]))
        self.assertEqual(steps[download]["with"]["run-id"], probe.RESUME_ORIGIN["GITHUB_RUN_ID"])
        self.assertEqual(steps[download]["with"]["digest-mismatch"], "error")
        restore = next(step for step in steps if " restore " in step.get("run", ""))
        self.assertIn("--resume-metadata resume-source/resume.json", restore["run"])


if __name__ == "__main__":
    unittest.main()
