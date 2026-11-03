from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "etf-allocation-digest.yml"


class EtfWorkflowScheduleTests(unittest.TestCase):
    def test_etf_native_fallback_is_daily_at_beijing_0600(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("# Cloudflare primary: 05:30 Beijing daily. Native fallback: 06:00 Beijing.", text)
        self.assertIn('- cron: "0 22 * * *"', text)
        self.assertNotIn('- cron: "0 21 * * *"', text)
        self.assertNotIn('- cron: "30 21 * * 1-5"', text)
        self.assertNotIn('- cron: "0 22 * * 1-5"', text)
        self.assertNotIn('- cron: "0 22 * * 0-5"', text)
        self.assertNotIn('- cron: "30 21 * * *"', text)


if __name__ == "__main__":
    unittest.main()
