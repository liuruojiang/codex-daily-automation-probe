from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ic-im-v1-4-daily-digest.yml"


class ICIMV14WorkflowTests(unittest.TestCase):
    def test_workflow_uses_r1_migration_persistence_and_gmail_gate(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("name: IC IM v1.4-r1 Post-Close Digest", text)
        self.assertIn('- cron: "0 12 * * *"', text)
        self.assertIn('"$(date +%H)" -lt 20', text)
        self.assertIn('SCHEDULED_ATTEMPT: ${{ github.event_name == \'schedule\' || inputs.external_schedule == true }}', text)
        self.assertLess(text.index('Scheduled ICIM delivery is not due'), text.index('python automation/scripts/check_ic_im_v1_4_delivery.py'))
        self.assertNotIn('- cron: "3 5 * * *"', text)
        self.assertNotIn('- cron: "18 5 * * *"', text)
        self.assertNotIn('- cron: "33 5 * * *"', text)
        self.assertIn('group: ic-im-v1-4-r1-post-close-digest', text)
        self.assertIn("PLANNED_BJ: \"20:00 Asia/Shanghai\"", text)
        self.assertIn("inputs.publication_mode || 'close_confirmed'", text)
        self.assertIn("default: close_confirmed", text)
        self.assertIn("external_schedule:", text)
        self.assertIn("inputs.external_schedule != true", text)
        self.assertNotIn("github.event_name == 'workflow_dispatch' || steps.calendar.outputs.trade_day == 'true'", text)
        self.assertIn("restore_ic_im_v1_4_ledger.py", text)
        self.assertIn("--artifact-name ic-im-v1-4-r1-ledger", text)
        self.assertIn("restore_ic_im_v1_3_ledger.py", text)
        self.assertIn("--artifact-name ic-im-v1-3-r7-put-monthly-fix1-ledger", text)
        self.assertIn("--artifact-name ic-im-v1-3-r7-ledger", text)
        self.assertIn("--before-created-at 2026-09-07T00:00:00Z", text)
        self.assertIn("--required", text)
        self.assertIn("migrate_ic_im_v1_3_r7_to_v1_4_r1_state.py", text)
        self.assertIn("--source legacy-state", text)
        self.assertIn("--target state", text)
        self.assertIn("run_ic_im_v1_4_github_digest.py", text)
        self.assertIn("ref: e4be0958c9ce16256a4c301dd0eb08409bb4c343", text)
        self.assertIn("ICIM_LEGULEGU_DAILY_SNAPSHOT: ${{ secrets.ICIM_LEGULEGU_DAILY_SNAPSHOT }}", text)
        self.assertIn("ICIM_CHINABOND_SNAPSHOT_FILE: strategy-artifacts/chinabond.json", text)
        self.assertIn("timeout --foreground 90s python -u strategy/ic_im_chinabond.py", text)
        self.assertIn('--expected-date "${{ steps.calendar.outputs.today }}"', text)
        self.assertIn("use disclosed frozen fallback", text)
        self.assertLess(text.index("python -u strategy/ic_im_chinabond.py"), text.index("python -u strategy/run_ic_im_v1_4_github_digest.py"))
        self.assertNotIn("ref: main", text)
        self.assertIn("strategy-artifacts/strategy-sha.txt", text)
        self.assertIn('strategy.BUILD_ID == "v1.4-20260918-r1-coreput3x-fixedshort95-fix3-iciv30-qdelta05"', text)
        self.assertIn('policy.RULE_REVISION == "ic_im_v1_4_iciv30_qdelta05_20260918_v1"', text)
        self.assertIn('runner.DELIVERY_REVISION == "20260918-v14-coreput3x-fixedshort95-fix3-iciv30-qdelta05"', text)
        self.assertNotIn("published v1.4 fix2", (ROOT / "scripts" / "build_ic_im_v1_4_digest.py").read_text(encoding="utf-8"))
        self.assertNotIn("published v1.4 fix2", (ROOT / "scripts" / "prepare_ic_im_v1_4_marker.py").read_text(encoding="utf-8"))
        self.assertIn('strategy.GRID_POLICY_REVISION == "ic_im_im_grid160_half_20260914_v1"', text)
        self.assertIn('strategy.IM_EXECUTION_FIX_REVISION == "im_put_execution_guards_20260908_v2"', text)
        self.assertIn('strategy.IM_PUT_EXECUTION_REVISION == "im_monthly_reset_20260907_v1"', text)
        self.assertLess(text.index("Install strategy dependencies"), text.index("Verify corrected IM Put build"))
        self.assertIn('strategy._latest_completed_exchange_day(clock)', text)
        self.assertIn("steps.calendar.outputs.completed_day", text)
        self.assertIn("steps.publication_mode.outputs.mode == 'realtime'", text)
        self.assertIn('ICIM_REQUIRE_MIGRATION: "1"', text)
        self.assertIn("ICIM_STATE_DIR: state", text)
        self.assertLess(text.index("Verify v1.4 ledger chain"), text.index("Run IC/IM signal"))
        self.assertIn("build_ic_im_v1_4_digest.py", text)
        self.assertIn("prepare_ic_im_v1_4_marker.py", text)
        self.assertIn("state/migration_record.json", text)
        self.assertIn("name: ic-im-v1-4-r1-ledger", text)
        self.assertIn("steps.send_gmail.outcome == 'success'", text)
        self.assertIn("steps.build_digest.outcome == 'success'", text)
        self.assertIn("name: ic-im-v1-4-r1-post-close-digest", text)


if __name__ == "__main__":
    unittest.main()
