from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "microcap-realtime-digest.yml"


class MicrocapWorkflowRefreshGateTests(unittest.TestCase):
    def test_microcap_workflow_refreshes_state_before_signals(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("# Redundant off-minute triggers", text)
        self.assertIn('- cron: "33 1 * * *"', text)
        self.assertIn('- cron: "48 1 * * *"', text)
        self.assertIn('- cron: "3 2 * * *"', text)
        self.assertIn('PLANNED_BJ: "09:33/09:48/10:03 Asia/Shanghai"', text)
        self.assertNotIn('10:00 Asia/Shanghai', text)
        self.assertNotIn('- cron: "0 2 * * *"', text)
        self.assertNotIn('10:30 Asia/Shanghai', text)
        self.assertNotIn('- cron: "30 2 * * *"', text)
        self.assertIn("name: Check A-share trading day", text)
        self.assertIn("ak.tool_trade_date_hist_sina", text)
        self.assertIn("SHOULD_RUN_MICROCAP=false", text)
        self.assertIn("needs: check-trading-day", text)
        self.assertIn("github.event_name == 'workflow_dispatch' || needs.check-trading-day.outputs.SHOULD_RUN_MICROCAP == 'true'", text)
        self.assertIn("name: Refresh Top100 realtime state", text)
        self.assertIn('"pandas<3"', text)
        self.assertIn('"akshare==1.18.46"', text)
        self.assertIn("scripts/realtime_state_bundle.py refresh --root .", text)
        refresh_step = text[
            text.index("name: Refresh Top100 realtime state") : text.index("name: Record refresh failure for digest")
        ]
        self.assertIn("--max-workers 1", refresh_step)
        self.assertNotIn("--max-workers 2", refresh_step)
        self.assertNotIn("microcap_top100_mom16_biweekly_live_v2_0.py", refresh_step)
        self.assertNotIn("&& python", refresh_step)
        self.assertIn("name: Run v2.0 realtime signal", text)
        self.assertIn("name: Run v2.3 realtime signal", text)
        self.assertIn("name: Run v2.5 realtime signal", text)
        self.assertIn("microcap_top100_mom16_biweekly_live_v2_5.py", text)
        self.assertIn("--result v2.5=microcap/realtime_signal_v2_5_result.txt", text)
        self.assertIn(
            "--signal-csv v2.0=microcap/outputs/microcap_top100_mom16_biweekly_live_v2_0_realtime_signal.csv",
            text,
        )
        self.assertIn(
            "--signal-csv v2.3=microcap/outputs/microcap_top100_mom16_biweekly_live_v2_3_realtime_signal.csv",
            text,
        )
        self.assertIn(
            "--signal-csv v2.5=microcap/outputs/microcap_top100_mom16_biweekly_live_v2_5_realtime_signal.csv",
            text,
        )
        self.assertIn("--exit-code \"v2.5=${SIGNAL_V2_5_EXIT_CODE:-unknown}\"", text)
        self.assertIn('id: microcap_sha', text)
        self.assertIn('git rev-parse HEAD', text)
        self.assertIn('--strategy-sha "${{ steps.microcap_sha.outputs.sha }}"', text)
        self.assertIn('repository: liuruojiang/microcap', text)
        self.assertIn('ref: b53fe0a956d7a679b04fe14aef8970c2fa940d19', text)
        self.assertNotIn('ref: main', text)
        sha_step = text[text.index('name: Record microcap strategy SHA') : text.index('name: Install runtime dependencies')]
        self.assertIn('working-directory: microcap', sha_step)
        self.assertIn('echo "sha=$(git rev-parse HEAD)" >> "${GITHUB_OUTPUT}"', sha_step)
        self.assertIn("microcap/realtime_signal_v2_5_result.txt", text)
        self.assertNotIn("v2.4", text)
        self.assertLess(
            text.index("name: Refresh Top100 realtime state"),
            text.index("name: Run v2.0 realtime signal"),
        )
        self.assertNotIn("if: steps.refresh_state.outputs.exit_code == '0'", text)
        self.assertIn("group: microcap-realtime-digest", text)
        self.assertIn("cancel-in-progress: false", text)
        self.assertIn("actions: read", text)
        self.assertIn("name: Check delivery marker", text)
        self.assertIn("scripts/check_microcap_delivery.py", text)
        self.assertIn("steps.delivery_gate.outputs.should_send == 'true'", text)
        self.assertIn("name: Mark digest delivered", text)
        self.assertIn("steps.delivery_gate.outputs.marker_name", text)
        self.assertIn("subject_prefix", text)
        self.assertIn("--subject-prefix", text)
        self.assertIn("name: Record refresh failure for digest", text)
        self.assertIn("for version in v2_0 v2_3 v2_5", text)
        self.assertEqual(text.count('TOP100_REALTIME_REQUIRE_STATE: "1"'), 3)
        self.assertEqual(text.count("timeout --foreground 8m python -u microcap_top100"), 3)
        self.assertNotIn("timeout --foreground 30m python -u microcap_top100", text)
        self.assertIn(
            "if: steps.delivery_gate.outputs.should_send == 'true' && (steps.signals_v20.outputs.exit_code != '0' || steps.signals_v23.outputs.exit_code != '0' || steps.signals_v25.outputs.exit_code != '0')",
            text,
        )


if __name__ == "__main__":
    unittest.main()
