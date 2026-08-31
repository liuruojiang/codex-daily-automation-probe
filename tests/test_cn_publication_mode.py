from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import resolve_cn_publication_mode as resolver  # noqa: E402


BJ = ZoneInfo("Asia/Shanghai")


class PublicationModeTests(unittest.TestCase):
    def test_scheduled_run_inside_continuous_session_is_realtime(self) -> None:
        mode, basis = resolver.resolve_mode(
            "auto", datetime(2026, 8, 28, 13, 45, tzinfo=BJ)
        )
        self.assertEqual(mode, "realtime")
        self.assertEqual(basis, "scheduled_run_inside_continuous_session")

    def test_delayed_midnight_run_falls_back_to_close_confirmed(self) -> None:
        mode, basis = resolver.resolve_mode(
            "auto", datetime(2026, 8, 28, 0, 45, tzinfo=BJ)
        )
        self.assertEqual(mode, "close_confirmed")
        self.assertEqual(basis, "scheduled_run_outside_continuous_session")

    def test_lunch_break_and_weekend_are_close_confirmed(self) -> None:
        for value in (
            datetime(2026, 8, 28, 12, 0, tzinfo=BJ),
            datetime(2026, 8, 29, 13, 45, tzinfo=BJ),
        ):
            with self.subTest(value=value):
                self.assertEqual(resolver.resolve_mode("auto", value)[0], "close_confirmed")

    def test_manual_mode_is_preserved(self) -> None:
        value = datetime(2026, 8, 29, 2, 0, tzinfo=BJ)
        self.assertEqual(
            resolver.resolve_mode("realtime", value),
            ("realtime", "manual_request"),
        )
        self.assertEqual(
            resolver.resolve_mode("close_confirmed", value),
            ("close_confirmed", "manual_request"),
        )


if __name__ == "__main__":
    unittest.main()
