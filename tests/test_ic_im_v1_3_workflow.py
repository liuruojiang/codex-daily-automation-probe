from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ic-im-v1-3-daily-digest.yml"


class ICIMV13WorkflowTests(unittest.TestCase):
    def test_workflow_uses_r7_migration_persistence_and_gmail_gate(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("name: IC IM v1.3-r7 Post-Close Digest", text)
        self.assertIn('- cron: "0 12 * * *"', text)
        self.assertIn('"$(date +%H)" -lt 20', text)
        self.assertIn('SCHEDULED_ATTEMPT: ${{ github.event_name == \'schedule\' || inputs.external_schedule == true }}', text)
        self.assertLess(text.index('Scheduled ICIM delivery is not due'), text.index('python automation/scripts/check_ic_im_v1_3_delivery.py'))
        self.assertNotIn('- cron: "3 5 * * *"', text)
        self.assertNotIn('- cron: "18 5 * * *"', text)
        self.assertNotIn('- cron: "33 5 * * *"', text)
        self.assertIn('group: ic-im-v1-3-r7-post-close-digest', text)
        self.assertIn("PLANNED_BJ: \"20:00 Asia/Shanghai\"", text)
        self.assertIn("inputs.publication_mode || 'close_confirmed'", text)
        self.assertIn("default: close_confirmed", text)
        self.assertIn("external_schedule:", text)
        self.assertIn("inputs.external_schedule != true", text)
        self.assertNotIn("github.event_name == 'workflow_dispatch' || steps.calendar.outputs.trade_day == 'true'", text)
        self.assertIn("restore_ic_im_v1_3_ledger.py", text)
        self.assertIn("--artifact-name ic-im-v1-3-r7-put-monthly-fix1-ledger", text)
        self.assertIn("--artifact-name ic-im-v1-3-r7-ledger", text)
        self.assertIn("--before-created-at 2026-09-07T00:00:00Z", text)
        self.assertIn("--required", text)
        self.assertNotIn('test "${{ steps.restore_prefx_ledger.outputs.restored }}"', text)
        self.assertIn("run_ic_im_v1_3_github_digest.py", text)
        self.assertIn("ref: 405e0bcb78c59eabeefd45b72fc8c0405a1a3e9d", text)
        self.assertIn("ICIM_LEGULEGU_DAILY_SNAPSHOT: ${{ secrets.ICIM_LEGULEGU_DAILY_SNAPSHOT }}", text)
        self.assertIn("ICIM_CHINABOND_SNAPSHOT_FILE: strategy-artifacts/chinabond.json", text)
        self.assertIn("timeout --foreground 90s python -u strategy/ic_im_chinabond.py", text)
        self.assertIn('--expected-date "${{ steps.calendar.outputs.today }}"', text)
        self.assertIn("use disclosed frozen fallback", text)
        self.assertLess(text.index("python -u strategy/ic_im_chinabond.py"), text.index("python -u strategy/run_ic_im_v1_3_github_digest.py"))
        self.assertNotIn("ref: main", text)
        self.assertIn("strategy-artifacts/strategy-sha.txt", text)
        self.assertIn('strategy.BUILD_ID == "v1.3-20260908-r7-mom120-put102-fix1"', text)
        self.assertIn('strategy.IM_EXECUTION_FIX_REVISION == "im_put_execution_guards_20260908_v2"', text)
        self.assertIn('strategy.IM_PUT_EXECUTION_REVISION == "im_monthly_reset_20260907_v1"', text)
        self.assertLess(text.index("Install strategy dependencies"), text.index("Verify corrected IM Put build"))
        self.assertIn('--expected-market-date "${{ steps.calendar.outputs.today }}"', text)
        self.assertIn('ICIM_REQUIRE_MIGRATION: "1"', text)
        self.assertIn("ICIM_STATE_DIR: state", text)
        self.assertIn("build_ic_im_v1_3_digest.py", text)
        self.assertIn("prepare_ic_im_v1_3_marker.py", text)
        self.assertIn("state/migration_record.json", text)
        self.assertIn("name: ic-im-v1-3-r7-put-monthly-fix1-ledger", text)
        self.assertIn("steps.send_gmail.outcome == 'success'", text)
        self.assertIn("steps.build_digest.outcome == 'success'", text)
        self.assertIn("name: ic-im-v1-3-r7-post-close-digest", text)


if __name__ == "__main__":
    unittest.main()
