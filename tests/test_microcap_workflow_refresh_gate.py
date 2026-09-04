from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "microcap-realtime-digest.yml"


class MicrocapWorkflowRefreshGateTests(unittest.TestCase):
    def test_microcap_workflow_refreshes_state_before_signals(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn('- cron: "0 10 * * *"', text)
        self.assertNotIn('- cron: "3 5 * * *"', text)
        self.assertNotIn('- cron: "18 5 * * *"', text)
        self.assertNotIn('- cron: "33 5 * * *"', text)
        self.assertIn('PLANNED_BJ: "18:00 Asia/Shanghai"', text)
        self.assertNotIn('- cron: "0 2 * * *"', text)
        self.assertNotIn('10:30 Asia/Shanghai', text)
        self.assertNotIn('- cron: "30 2 * * *"', text)
        self.assertIn("name: Check A-share trading day", text)
        self.assertIn("ak.tool_trade_date_hist_sina", text)
        self.assertIn("SHOULD_RUN_MICROCAP=false", text)
        self.assertIn("needs: [check-trading-day, regression]", text)
        self.assertIn("external_schedule:", text)
        self.assertIn("inputs.external_schedule != true", text)
        self.assertIn("needs.check-trading-day.outputs.SHOULD_RUN_MICROCAP == 'true'", text)
        self.assertIn("name: Refresh Top100 realtime state", text)
        self.assertIn('"pandas<3"', text)
        self.assertIn('"akshare==1.18.46"', text)
        self.assertIn("scripts/realtime_state_bundle.py refresh", text)
        self.assertIn("--root .", text)
        refresh_step = text[
            text.index("name: Refresh Top100 realtime state") : text.index("name: Record refresh failure for digest")
        ]
        self.assertIn("--max-workers 1", refresh_step)
        self.assertIn("--force-refresh-static-inputs", refresh_step)
        self.assertIn("for attempt in 1 2 3", refresh_step)
        self.assertIn("timeout --foreground 22m", refresh_step)
        self.assertNotIn("publication_mode.outputs.mode == 'realtime'", refresh_step)
        self.assertNotIn("--max-workers 2", refresh_step)
        self.assertNotIn("microcap_top100_mom16_biweekly_live_v2_0.py", refresh_step)
        self.assertNotIn("&& python", refresh_step)
        self.assertIn("name: Run v2.0 selected signal", text)
        self.assertIn("name: Run v2.3 selected signal", text)
        self.assertIn("name: Run v2.5 selected signal", text)
        self.assertIn("microcap_top100_mom16_biweekly_live_v2_5.py", text)
        self.assertIn("--result v2.5=${v25_root}/realtime_signal_v2_5_result.txt", text)
        self.assertIn(
            "--signal-csv v2.0=microcap/outputs/microcap_top100_mom16_biweekly_live_v2_0_${signal_suffix}.csv",
            text,
        )
        self.assertIn(
            "--signal-csv v2.3=${v23_root}/outputs/microcap_top100_mom16_biweekly_live_v2_3_${signal_suffix}.csv",
            text,
        )
        self.assertIn(
            "--signal-csv v2.5=${v25_root}/outputs/microcap_top100_mom16_biweekly_live_v2_5_${signal_suffix}.csv",
            text,
        )
        self.assertIn("--exit-code \"v2.5=${SIGNAL_V2_5_EXIT_CODE:-unknown}\"", text)
        self.assertIn('id: microcap_sha', text)
        self.assertIn('git rev-parse HEAD', text)
        self.assertIn('--strategy-sha "${{ steps.microcap_sha.outputs.sha }}"', text)
        self.assertIn('repository: liuruojiang/microcap', text)
        self.assertEqual(text.count('ref: 3d01422edd59f721e093eb8ccf46d8b24e3a4097'), 3)
        self.assertNotIn('ref: main', text)
        self.assertIn("name: Check out isolated v2.3 close-confirmed workspace", text)
        self.assertIn("name: Check out isolated v2.5 close-confirmed workspace", text)
        self.assertIn("path: microcap-v23", text)
        self.assertIn("path: microcap-v25", text)
        self.assertIn("mode == 'close_confirmed' && 'microcap-v23' || 'microcap'", text)
        self.assertIn("mode == 'close_confirmed' && 'microcap-v25' || 'microcap'", text)
        self.assertIn("--result v2.3=${v23_root}/realtime_signal_v2_3_result.txt", text)
        self.assertIn("--result v2.5=${v25_root}/realtime_signal_v2_5_result.txt", text)
        self.assertIn("--signal-csv v2.3=${v23_root}/outputs/", text)
        self.assertIn("--signal-csv v2.5=${v25_root}/outputs/", text)
        sha_step = text[text.index('name: Record microcap strategy SHA') : text.index('name: Install runtime dependencies')]
        self.assertIn('working-directory: microcap', sha_step)
        self.assertIn('echo "sha=$(git rev-parse HEAD)" >> "${GITHUB_OUTPUT}"', sha_step)
        self.assertIn("microcap/realtime_signal_v2_5_result.txt", text)
        self.assertNotIn("v2.4", text)
        self.assertLess(
            text.index("name: Refresh Top100 realtime state"),
            text.index("name: Run v2.0 selected signal"),
        )
        self.assertIn("steps.state_bundle.outcome == 'success'", text)
        self.assertIn("name: Pack validated production state", text)
        self.assertIn("name: Restore state into isolated v2.3 workspace", text)
        self.assertIn("name: Restore state into isolated v2.5 workspace", text)
        self.assertLess(
            text.index("name: Restore state into isolated v2.3 workspace"),
            text.index("name: Run v2.3 selected signal"),
        )
        self.assertLess(
            text.index("name: Restore state into isolated v2.5 workspace"),
            text.index("name: Run v2.5 selected signal"),
        )
        self.assertIn("group: microcap-realtime-digest", text)
        self.assertIn("cancel-in-progress: false", text)
        self.assertIn("actions: write", text)
        self.assertIn("uses: actions/cache/restore@v4", text)
        self.assertIn("uses: actions/cache/save@v4", text)
        self.assertIn("name: Bootstrap full rebalance cache on cold start", text)
        self.assertIn("microcap-full-rebalance-cache-v1-20260903", text)
        self.assertIn("microcap-full-rebalance-cache-v1-20260903.zip", text)
        self.assertIn("ecee736f9bb35ac4b43a63e4fd7c83b0a4e9d8c94903f2631b5763a4e1d46686", text)
        self.assertIn("scripts/full_rebalance_cache_bundle.py restore", text)
        self.assertIn("scripts/full_rebalance_cache_bundle.py validate", text)
        self.assertIn("name: Check delivery marker", text)
        self.assertIn("scripts/check_microcap_delivery.py", text)
        self.assertIn("steps.delivery_gate.outputs.should_send == 'true'", text)
        self.assertIn("name: Mark digest delivered", text)
        self.assertIn("steps.delivery_gate.outputs.marker_name", text)
        self.assertIn("subject_prefix", text)
        self.assertIn("--subject-prefix", text)
        self.assertIn("name: Record refresh failure for digest", text)
        self.assertIn('for item in "microcap:v2_0" "microcap-v23:v2_3" "microcap-v25:v2_5"', text)
        self.assertEqual(text.count('TOP100_REALTIME_REQUIRE_STATE: "1"'), 3)
        self.assertEqual(text.count("timeout --foreground 25m python -u microcap_top100"), 6)
        self.assertNotIn("timeout --foreground 60m python -u microcap_top100", text)
        self.assertIn("publication_mode:", text)
        self.assertIn("- close_confirmed", text)
        self.assertIn("resolve_cn_publication_mode.py", text)
        self.assertIn("inputs.publication_mode || 'close_confirmed'", text)
        self.assertIn("default: close_confirmed", text)
        self.assertIn("--publication-mode \"${{ steps.publication_mode.outputs.mode }}\"", text)
        self.assertIn("--expected-signal-date", text)
        self.assertIn("github.event_name == 'schedule' || inputs.external_schedule == true", text)
        self.assertIn("steps.delivery_gate.outputs.delivery_date", text)
        self.assertIn("signal_suffix=latest_signal", text)
        self.assertIn("steps.digest.outputs.status != 'OK'", text)
        self.assertIn("steps.refresh_state.outcome == 'success'", text)
        fail_step = text[text.index("name: Fail job when signal publication failed") :]
        self.assertIn("if: always()", fail_step)

    def test_normal_delivery_marker_requires_all_signal_scripts_to_succeed(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        prepare_step = text[
            text.index("name: Prepare delivery marker") : text.index("name: Mark digest delivered")
        ]
        mark_step = text[
            text.index("name: Mark digest delivered") : text.index("name: Upload signal outputs")
        ]

        for step in (prepare_step, mark_step):
            self.assertIn("steps.send_gmail.outcome == 'success'", step)
            self.assertIn("steps.digest.outputs.status == 'OK'", step)
            self.assertIn("steps.signals_v20.outputs.exit_code == '0'", step)
            self.assertIn("steps.signals_v23.outputs.exit_code == '0'", step)
            self.assertIn("steps.signals_v25.outputs.exit_code == '0'", step)
            self.assertIn("steps.whole_delivery.outputs.exit_code == '0'", step)

    def test_whole_delivery_is_verified_before_email_and_retained(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertLess(text.index("name: Verify and pack all three final deliveries"),
                        text.index("name: Build Markdown digest"))
        self.assertIn("scripts/top100_cloud_delivery.py pack", text)
        self.assertIn('--expected-date "${{ steps.delivery_gate.outputs.delivery_date }}"', text)
        self.assertIn("SIGNAL_V2_${version}_EXIT_CODE=whole_delivery_failed", text)
        self.assertIn("name: microcap-whole-delivery-state", text)
        self.assertIn("microcap/whole_delivery_result.txt", text)

    def test_close_confirmed_cold_outputs_require_a_second_audited_generation(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        for version in ("0", "3", "5"):
            step = text.split(f"name: Run v2.{version} selected signal", 1)[1].split("\n      - name:", 1)[0]
            self.assertEqual(step.count(f"python -u microcap_top100_mom16_biweekly_live_v2_{version}.py"), 2)
            self.assertIn('if [[ "${status}" -eq 0 && "${{ steps.publication_mode.outputs.mode }}" == "close_confirmed" ]]', step)
            self.assertIn(f"tee -a realtime_signal_v2_{version}_result.txt", step)
            self.assertLess(step.rindex("status=${PIPESTATUS[0]}"), step.index('echo "exit_code=${status}"'))


if __name__ == "__main__":
    unittest.main()
