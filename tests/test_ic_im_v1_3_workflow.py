from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ic-im-v1-3-daily-digest.yml"


class ICIMV13WorkflowTests(unittest.TestCase):
    def test_workflow_uses_r7_migration_persistence_and_gmail_gate(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("name: IC IM v1.3-r7 Post-Close Digest", text)
        self.assertIn('- cron: "0 10 * * *"', text)
        self.assertNotIn('- cron: "3 5 * * *"', text)
        self.assertNotIn('- cron: "18 5 * * *"', text)
        self.assertNotIn('- cron: "33 5 * * *"', text)
        self.assertIn('group: ic-im-v1-3-r7-post-close-digest', text)
        self.assertIn("PLANNED_BJ: \"18:00 Asia/Shanghai\"", text)
        self.assertIn("inputs.publication_mode || 'close_confirmed'", text)
        self.assertIn("default: close_confirmed", text)
        self.assertIn("external_schedule:", text)
        self.assertIn("inputs.external_schedule != true", text)
        self.assertNotIn("github.event_name == 'workflow_dispatch' || steps.calendar.outputs.trade_day == 'true'", text)
        self.assertIn("restore_ic_im_v1_3_ledger.py", text)
        self.assertIn("migrate_ic_im_v1_3_r6_to_r7_state.py", text)
        self.assertIn("--artifact-name ic-im-v1-3-r6-ledger", text)
        self.assertIn("run_ic_im_v1_3_github_digest.py", text)
        self.assertIn("ref: 2ff781f348aa8ebe1925671881b47c2c8c317000", text)
        self.assertNotIn("ref: main", text)
        self.assertIn("strategy-artifacts/strategy-sha.txt", text)
        self.assertIn('--expected-market-date "${{ steps.calendar.outputs.today }}"', text)
        self.assertIn('ICIM_REQUIRE_MIGRATION: "1"', text)
        self.assertIn("ICIM_STATE_DIR: state", text)
        self.assertIn("build_ic_im_v1_3_digest.py", text)
        self.assertIn("prepare_ic_im_v1_3_marker.py", text)
        self.assertIn("state/migration_record.json", text)
        self.assertIn("name: ic-im-v1-3-r7-ledger", text)
        self.assertIn("steps.send_gmail.outcome == 'success'", text)
        self.assertIn("steps.build_digest.outcome == 'success'", text)
        self.assertIn("name: ic-im-v1-3-r7-post-close-digest", text)


if __name__ == "__main__":
    unittest.main()
