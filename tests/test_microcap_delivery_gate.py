from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check_microcap_delivery as gate  # noqa: E402


class MicrocapDeliveryGateTests(unittest.TestCase):
    def test_delivery_date_and_marker_use_beijing_calendar_date(self) -> None:
        now = datetime(2026, 8, 6, 16, 30, tzinfo=timezone.utc)

        delivery_date = gate.beijing_delivery_date(now)

        self.assertEqual(delivery_date.isoformat(), "2026-08-07")
        self.assertEqual(
            gate.delivery_marker_name(delivery_date),
            "microcap-realtime-digest-delivered-2026-08-07",
        )

    def test_nonexpired_matching_artifact_blocks_redundant_delivery(self) -> None:
        payload = {
            "artifacts": [
                {
                    "name": "microcap-realtime-digest-delivered-2026-08-07",
                    "expired": False,
                }
            ]
        }

        self.assertTrue(
            gate.marker_exists(payload, "microcap-realtime-digest-delivered-2026-08-07")
        )
        self.assertFalse(
            gate.should_send(correction=False, marker_already_exists=True)
        )

    def test_correction_dispatch_bypasses_existing_marker_and_network_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "github-output.txt"
            with (
                patch.object(sys, "argv", ["check_microcap_delivery.py", "--correction"]),
                patch.dict(
                    os.environ,
                    {
                        "GITHUB_OUTPUT": str(output_path),
                        "GITHUB_REPOSITORY": "liuruojiang/codex-daily-automation-probe",
                    },
                    clear=False,
                ),
                patch.object(gate, "fetch_artifacts") as fetch_artifacts,
                patch.object(
                    gate,
                    "now_utc",
                    return_value=datetime(2026, 8, 7, 1, 35, tzinfo=timezone.utc),
                ),
            ):
                self.assertEqual(gate.main(), 0)

            output = output_path.read_text(encoding="utf-8")

        fetch_artifacts.assert_not_called()
        self.assertIn("should_send=true", output)
        self.assertIn("subject_prefix=纠正版", output)

    def test_accepted_smtp_receipt_suppresses_duplicate_send(self) -> None:
        day = datetime(2026, 8, 7, 9, tzinfo=timezone.utc)
        marker_name = gate.delivery_marker_name(day.date(), "close_confirmed")
        payloads = {
            marker_name: {"artifacts": []},
            marker_name + "-smtp-accepted": {
                "artifacts": [{"name": marker_name + "-smtp-accepted", "expired": False}]
            },
        }
        outputs: list[dict[str, str]] = []
        with (
            patch.object(gate, "now_utc", return_value=day),
            patch.object(gate, "fetch_artifacts", side_effect=lambda _repo, _token, name, _api: payloads.get(name, {"artifacts": []})),
            patch.object(sys, "argv", ["check_microcap_delivery.py", "--publication-mode", "close_confirmed", "--repository", "o/r", "--token", "t"]),
            patch.object(gate, "write_outputs", outputs.append),
        ):
            self.assertEqual(gate.main(), 0)
        self.assertEqual(outputs[0]["should_send"], "false")
        self.assertEqual(outputs[0]["recover_marker"], "true")


if __name__ == "__main__":
    unittest.main()
