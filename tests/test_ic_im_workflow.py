from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ic-im-v1-2-daily-digest.yml"


class ICIMWorkflowTests(unittest.TestCase):
    def test_workflow_has_persistence_retries_and_gmail_gate(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('- cron: "30 8 * * 1-5"', text)
        self.assertIn('- cron: "0 9 * * 1-5"', text)
        self.assertIn('- cron: "30 9 * * 1-5"', text)
        self.assertIn("group: ic-im-v1-2-daily-digest", text)
        self.assertIn("cancel-in-progress: false", text)
        self.assertIn("secrets.ICIM_REPO_TOKEN", text)
        self.assertIn("scripts/restore_ic_im_ledger.py", text)
        self.assertIn("run_ic_im_v1_2_github_digest.py", text)
        self.assertIn("name: ic-im-v1-2-ledger", text)
        self.assertIn("retention-days: 90", text)
        self.assertIn("scripts/check_ic_im_delivery.py", text)
        self.assertIn("scripts/send_report.py", text)
        self.assertIn("steps.attempt.outputs.final_attempt == 'true'", text)
        self.assertIn("steps.run_signal.outputs.exit_code == '0'", text)
        self.assertIn("name: Mark digest delivered", text)


if __name__ == "__main__":
    unittest.main()
