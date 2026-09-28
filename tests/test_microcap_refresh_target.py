from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_microcap_refresh_target import check_report  # noqa: E402


class MicrocapRefreshTargetTests(unittest.TestCase):
    def report(self, anchor: str, proof: str | None = None) -> dict[str, object]:
        return {
            "ok": True,
            "anchor_dates": {
                "proxy_index": anchor,
                "costed_nav": anchor,
                "panel_shadow": anchor,
            },
            "refresh_proof": {"target_end_date": proof or anchor},
        }

    def test_long_holiday_gaps_pass_when_latest_completed_close_is_present(self) -> None:
        for previous, completed in (
            ("2026-09-30", "2026-10-08"),
            ("2027-01-29", "2027-02-12"),
        ):
            with self.subTest(previous=previous, completed=completed):
                self.assertGreater((date.fromisoformat(completed) - date.fromisoformat(previous)).days, 3)
                check_report(self.report(completed), completed)

    def test_previous_close_cannot_be_published_after_a_long_holiday(self) -> None:
        for name in ("proxy_index", "costed_nav", "panel_shadow"):
            with self.subTest(name=name):
                report = self.report("2026-10-08")
                report["anchor_dates"][name] = "2026-09-30"
                with self.assertRaisesRegex(ValueError, f"{name} anchor"):
                    check_report(report, "2026-10-08")

    def test_current_csvs_cannot_borrow_an_old_refresh_proof(self) -> None:
        with self.assertRaisesRegex(ValueError, "refresh proof"):
            check_report(self.report("2026-10-08", proof="2026-09-30"), "2026-10-08")

    def test_failed_state_validation_cannot_pass_date_check(self) -> None:
        report = self.report("2026-10-08")
        report["ok"] = False
        with self.assertRaisesRegex(ValueError, "did not pass"):
            check_report(report, "2026-10-08")
