from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ic-im-v1-3-daily-digest.yml"


class ICIMV13WorkflowTests(unittest.TestCase):
    def test_workflow_uses_r5_migration_persistence_and_gmail_gate(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("name: IC IM v1.3-r5 Realtime Digest", text)
        self.assertIn('group: ic-im-v1-3-r5-realtime-digest', text)
        self.assertIn("restore_ic_im_v1_3_ledger.py", text)
        self.assertIn("migrate_ic_im_v1_2_to_v1_3_state.py", text)
        self.assertIn("run_ic_im_v1_3_github_digest.py", text)
        self.assertIn('ICIM_REQUIRE_MIGRATION: "1"', text)
        self.assertIn("build_ic_im_v1_3_digest.py", text)
        self.assertIn("prepare_ic_im_v1_3_marker.py", text)
        self.assertIn("state/migration_record.json", text)
        self.assertIn("name: ic-im-v1-3-ledger", text)
        self.assertIn("steps.send_gmail.outcome == 'success'", text)


if __name__ == "__main__":
    unittest.main()
