"""Adversarial regression checks for snapshot isolation and delivery workflow."""

import copy
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch

import monitor
from test_advisory import sample


class SnapshotDeliveryAuditTests(unittest.TestCase):
    def candidate(self):
        prior = copy.deepcopy(sample())
        prior["method_version"] = monitor.METHOD_VERSION
        prior["generated_at_utc"] = "2026-10-12T06:00:00+00:00"
        return prior

    def invoke(self, candidate, label=""):
        written = {}

        def capture(path, text, **kwargs):
            written[path.name] = text
            return len(text)

        payload = candidate if isinstance(candidate, str) else json.dumps(candidate)
        with patch("monitor.build_report", return_value=copy.deepcopy(sample())), \
                patch.object(Path, "mkdir"), patch.object(Path, "exists", return_value=True), \
                patch.object(Path, "read_text", return_value=payload), \
                patch.object(Path, "write_text", autospec=True, side_effect=capture), \
                patch("builtins.print"):
            result = monitor.main(["--output-dir", "audit-output", "--previous-report", "audit-prior.json",
                                   "--report-label", label])
        self.assertEqual(result, 0)
        self.assertEqual(set(written), {"report.json", "report.txt", "report.html", "metadata.json"})
        report = json.loads(written["report.json"])
        metadata = json.loads(written["metadata.json"])
        self.assertEqual(metadata["body"], written["report.txt"])
        self.assertEqual(metadata["html_body"], written["report.html"])
        return report, metadata

    def assert_rejected(self, candidate):
        report, _ = self.invoke(candidate)
        self.assertTrue(report["warnings"])
        changes = "\n".join(report["advisory"]["sections"][1][1])
        self.assertIn("没有有效的上期报告快照", changes)
        self.assertNotIn("对比快照：", changes)

    def test_structurally_damaged_snapshots_do_not_block_current_report(self):
        cases = [None, [], "{broken"]
        for key, value in (("core", None), ("core", []), ("supplemental", None),
                           ("sources", None), ("sources", [None]), ("sources", ["bad"])):
            prior = self.candidate()
            prior[key] = value
            cases.append(prior)
        prior = self.candidate()
        prior["core"]["cape_boundary_10y"] = None
        cases.append(prior)
        for i, prior in enumerate(cases):
            with self.subTest(case=i):
                self.assert_rejected(prior)

    def test_future_expired_naive_and_invalid_dates_do_not_become_baselines(self):
        for timestamp in ("2026-10-20T06:00:00+00:00", "2026-10-19T06:00:00+00:00",
                          "2026-10-05T05:59:59+00:00", "2026-10-12T06:00:00",
                          "invalid", None):
            with self.subTest(timestamp=timestamp):
                prior = self.candidate()
                prior["generated_at_utc"] = timestamp
                self.assert_rejected(prior)

    def test_different_or_missing_method_version_requires_new_baseline(self):
        for version in (None, "old-method-version", 1):
            with self.subTest(version=version):
                prior = self.candidate()
                prior["method_version"] = version
                self.assert_rejected(prior)

    def test_wrong_monitor_is_ignored(self):
        prior = self.candidate()
        prior["monitor"] = "Unrelated report"
        self.assert_rejected(prior)

    def test_valid_previous_report_is_used_through_fourteen_day_boundary(self):
        for timestamp in ("2026-10-12T06:00:00+00:00", "2026-10-05T06:00:00+00:00"):
            with self.subTest(timestamp=timestamp):
                prior = self.candidate()
                prior["generated_at_utc"] = timestamp
                report, _ = self.invoke(prior)
                changes = "\n".join(report["advisory"]["sections"][1][1])
                self.assertIn("对比快照：" + timestamp, changes)
                self.assertFalse(report["warnings"])

    def test_replay_label_is_visible_and_does_not_change_report_bodies(self):
        report, metadata = self.invoke(self.candidate(), "审计修订重发")
        self.assertTrue(metadata["subject"].endswith(" | 审计修订重发"))
        self.assertIn("本周结论", metadata["body"])
        self.assertIn(report["advisory"]["stance"], metadata["html_body"])


class WorkflowDeliveryAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        repository = Path(__file__).resolve().parents[2]
        cls.workflow = (repository / ".github/workflows/equity-momentum-boundary-weekly.yml").read_text(encoding="utf-8")

    def step(self, name):
        result = re.search(r"(?ms)^      - name: " + re.escape(name) + r"\n(.*?)(?=^      - name: |\Z)", self.workflow)
        self.assertIsNotNone(result, name)
        return result.group(1)

    def test_replay_push_is_main_and_exact_request_path_only(self):
        block = re.search(r"(?ms)^  push:\n(.*?)(?=^  schedule:)", self.workflow)
        self.assertIsNotNone(block)
        self.assertIn("branches: [main]", block.group(1))
        paths = re.findall(r"^      - (.+)$", block.group(1), re.M)
        self.assertEqual(paths, ["monitoring/equity_momentum_boundary/replay_request.json"])

    def test_only_this_workflow_and_branch_are_serialized_without_cancellation(self):
        block = re.search(r"(?ms)^concurrency:\n(.*?)(?=^jobs:)", self.workflow)
        self.assertIsNotNone(block)
        self.assertIn("group: equity-momentum-boundary-${{ github.ref }}", block.group(1))
        self.assertIn("cancel-in-progress: false", block.group(1))

    def test_snapshot_restore_and_save_have_unique_run_attempt_keys(self):
        restore = self.step("Restore previous delivered report")
        save = self.step("Save delivered snapshot")
        expected = "equity-boundary-report-v1-${{ github.ref_name }}-${{ github.run_id }}-${{ github.run_attempt }}"
        self.assertIn("key: " + expected, restore)
        self.assertIn("key: " + expected, save)
        self.assertIn("restore-keys: equity-boundary-report-v1-${{ github.ref_name }}-", restore)
        for block in (restore, save):
            self.assertIn("path: .monitor-state/report.json", block)

    def test_validation_only_never_sends_or_advances_delivered_snapshot(self):
        names = ("Send through the existing daily-report mailer", "Prepare delivered snapshot", "Save delivered snapshot")
        condition = "if: ${{ github.event_name != 'workflow_dispatch' || inputs.send_email != false }}"
        for name in names:
            with self.subTest(step=name):
                block = self.step(name)
                self.assertIn(condition, block)
                self.assertNotIn("always()", block)
                self.assertNotIn("continue-on-error:", block)
        positions = [self.workflow.index("- name: " + name) for name in names]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("scripts/send_report.py artifacts/metadata.json", self.step(names[0]))

    def test_ci_runs_real_unit_tests_on_python311_before_building_or_sending(self):
        self.assertIn('python-version: "3.11"', self.workflow)
        self.assertIn('python -m unittest discover -p "test_*.py"', self.step("Run unit tests"))
        self.assertLess(self.workflow.index("- name: Run unit tests"), self.workflow.index("- name: Build weekly report metadata"))
        self.assertIn("if: always()", self.step("Upload weekly report artifact"))


if __name__ == "__main__":
    unittest.main()
