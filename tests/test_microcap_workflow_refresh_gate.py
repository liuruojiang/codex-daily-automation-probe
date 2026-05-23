from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "microcap-realtime-digest.yml"


class MicrocapWorkflowRefreshGateTests(unittest.TestCase):
    def test_microcap_workflow_refreshes_state_before_signals(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("# 10:00 Asia/Shanghai == 02:00 UTC.", text)
        self.assertIn('- cron: "0 2 * * *"', text)
        self.assertIn('PLANNED_BJ: "10:00 Asia/Shanghai"', text)
        self.assertNotIn('10:30 Asia/Shanghai', text)
        self.assertNotIn('- cron: "30 2 * * *"', text)
        self.assertIn("name: Refresh Top100 realtime state", text)
        self.assertIn('"pandas<3"', text)
        self.assertIn('"akshare==1.18.46"', text)
        self.assertIn("scripts/realtime_state_bundle.py refresh --root .", text)
        self.assertIn("--max-workers 2", text)
        self.assertIn("microcap_top100_mom16_biweekly_live_v2_0.py --max-workers 2", text)
        self.assertIn('"信号"', text)
        self.assertIn("name: Run v2.0 realtime signal", text)
        self.assertIn("name: Run v2.3 realtime signal", text)
        self.assertIn("name: Run v2.5 realtime signal", text)
        self.assertIn("microcap_top100_mom16_biweekly_live_v2_5.py", text)
        self.assertIn("--result v2.5=microcap/realtime_signal_v2_5_result.txt", text)
        self.assertIn("--exit-code \"v2.5=${SIGNAL_V2_5_EXIT_CODE:-unknown}\"", text)
        self.assertIn("microcap/realtime_signal_v2_5_result.txt", text)
        self.assertNotIn("v2.4", text)
        self.assertLess(
            text.index("name: Refresh Top100 realtime state"),
            text.index("name: Run v2.0 realtime signal"),
        )
        self.assertNotIn("if: steps.refresh_state.outputs.exit_code == '0'", text)
        self.assertEqual(text.count("if: always()"), 7)
        self.assertIn("name: Record refresh failure for digest", text)
        self.assertIn("for version in v2_0 v2_3 v2_5", text)
        self.assertEqual(text.count('TOP100_REALTIME_REQUIRE_STATE: "1"'), 3)
        self.assertEqual(text.count("timeout --foreground 8m python -u microcap_top100"), 3)
        self.assertNotIn("timeout --foreground 30m python -u microcap_top100", text)
        self.assertIn(
            "if: steps.signals_v20.outputs.exit_code != '0' || steps.signals_v23.outputs.exit_code != '0' || steps.signals_v25.outputs.exit_code != '0'",
            text,
        )


if __name__ == "__main__":
    unittest.main()
