from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "etf-allocation-digest.yml"


class EtfWorkflowScheduleTests(unittest.TestCase):
    def test_etf_digest_does_not_schedule_on_beijing_sunday(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("# 06:00 Asia/Shanghai Monday-Saturday == 22:00 UTC Sunday-Friday.", text)
        self.assertIn('- cron: "0 22 * * 0-5"', text)
        self.assertNotIn('- cron: "0 22 * * *"', text)


if __name__ == "__main__":
    unittest.main()
