from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "etf-allocation-digest.yml"


class EtfWorkflowScheduleTests(unittest.TestCase):
    def test_etf_digest_only_schedules_on_beijing_tuesday_to_saturday(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("# 05:30 Asia/Shanghai Tuesday-Saturday == 21:30 UTC Monday-Friday.", text)
        self.assertIn('- cron: "30 21 * * 1-5"', text)
        self.assertNotIn('- cron: "0 22 * * 1-5"', text)
        self.assertNotIn('- cron: "0 22 * * 0-5"', text)
        self.assertNotIn('- cron: "0 22 * * *"', text)


if __name__ == "__main__":
    unittest.main()
