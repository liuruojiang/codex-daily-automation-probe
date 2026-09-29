from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "etf-allocation-digest.yml"


class EtfWorkflowScheduleTests(unittest.TestCase):
    def test_etf_digest_schedules_every_day_at_beijing_0530(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("# 05:30 Asia/Shanghai every day == 21:30 UTC on the previous calendar day.", text)
        self.assertIn('- cron: "30 21 * * *"', text)
        self.assertNotIn('- cron: "0 21 * * *"', text)
        self.assertNotIn('- cron: "30 21 * * 1-5"', text)
        self.assertNotIn('- cron: "0 22 * * 1-5"', text)
        self.assertNotIn('- cron: "0 22 * * 0-5"', text)
        self.assertNotIn('- cron: "0 22 * * *"', text)


if __name__ == "__main__":
    unittest.main()
