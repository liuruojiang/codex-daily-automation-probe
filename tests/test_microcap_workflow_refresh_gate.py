from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "microcap-realtime-digest.yml"


class MicrocapWorkflowRefreshGateTests(unittest.TestCase):
    def test_microcap_workflow_refreshes_state_before_signals(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("name: Refresh Top100 realtime state", text)
        self.assertIn("scripts/realtime_state_bundle.py refresh --root .", text)
        self.assertIn("name: Run v2.0 realtime signal", text)
        self.assertIn("name: Run v2.3 realtime signal", text)
        self.assertLess(
            text.index("name: Refresh Top100 realtime state"),
            text.index("name: Run v2.0 realtime signal"),
        )
        self.assertIn("if: steps.refresh_state.outputs.exit_code == '0'", text)
        self.assertIn("name: Record refresh failure for digest", text)
        self.assertEqual(text.count('TOP100_REALTIME_REQUIRE_STATE: "1"'), 2)
        self.assertEqual(text.count("timeout --foreground 8m python -u microcap_top100"), 2)
        self.assertNotIn("timeout --foreground 30m python -u microcap_top100", text)


if __name__ == "__main__":
    unittest.main()
