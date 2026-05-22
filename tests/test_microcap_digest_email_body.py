from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import build_microcap_realtime_digest as digest  # noqa: E402


class MicrocapDigestEmailBodyTests(unittest.TestCase):
    def test_metadata_body_contains_full_digest_without_markdown_attachment(self) -> None:
        with self.subTest("build digest"):
            import tempfile

            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                result_v20 = tmp_path / "v20.txt"
                result_v23 = tmp_path / "v23.txt"
                result_v24 = tmp_path / "v24.txt"
                out_dir = tmp_path / "artifacts"
                result_v20.write_text(
                    "\n".join(
                        [
                            "realtime_signal",
                            "strategy_version: v2.0",
                            "snapshot_time: 2026-05-19 15:31:56+08:00",
                            "current_holding: long_microcap_short_zz1000",
                            "microcap_mom: +12.3400%",
                            "hedge_mom: +4.5600%",
                            "momentum_gap: +7.7800%",
                        ]
                    ),
                    encoding="utf-8",
                )
                result_v23.write_text(
                    "\n".join(
                        [
                            "realtime_signal",
                            "strategy_version: v2.3",
                            "snapshot_time: 2026-05-19 15:34:29+08:00",
                            "next_holding: cash",
                        ]
                    ),
                    encoding="utf-8",
                )
                result_v24.write_text(
                    "\n".join(
                        [
                            "realtime_signal",
                            "strategy_version: v2.4",
                            "snapshot_time: 2026-05-19 15:36:11+08:00",
                            "current_holding: long_microcap_short_zz1000",
                            "next_holding: long_microcap_short_zz1000",
                        ]
                    ),
                    encoding="utf-8",
                )

                old_argv = sys.argv
                try:
                    sys.argv = [
                        "build_microcap_realtime_digest.py",
                        "--result",
                        f"v2.0={result_v20}",
                        "--result",
                        f"v2.3={result_v23}",
                        "--result",
                        f"v2.4={result_v24}",
                        "--out-dir",
                        str(out_dir),
                        "--planned",
                        "11:00 Asia/Shanghai",
                        "--started",
                        "2026-05-19 15:28:52 CST",
                        "--exit-code",
                        "v2.0=0",
                        "--exit-code",
                        "v2.3=0",
                        "--exit-code",
                        "v2.4=0",
                    ]
                    self.assertEqual(digest.main(), 0)
                finally:
                    sys.argv = old_argv

                meta = json.loads((out_dir / "metadata.json").read_text(encoding="utf-8"))
                v20_summary = digest.extract_signal_summary(result_v20.read_text(encoding="utf-8"))

        self.assertIn("# ", meta["body"])
        self.assertIn("## ", meta["body"])
        self.assertIn("strategy_version: v2.0", meta["body"])
        self.assertIn("strategy_version: v2.3", meta["body"])
        self.assertIn("strategy_version: v2.4", meta["body"])
        self.assertIn("microcap_mom: +12.3400%", v20_summary)
        self.assertIn("hedge_mom: +4.5600%", v20_summary)
        self.assertIn("momentum_gap: +7.7800%", v20_summary)
        self.assertNotIn("见附件", meta["body"])
        self.assertFalse(meta.get("attachment"))

    def test_stale_anchor_is_sent_with_visible_warning(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result_v20 = tmp_path / "v20.txt"
            out_dir = tmp_path / "artifacts"
            result_v20.write_text(
                "\n".join(
                    [
                        "realtime_signal",
                        "strategy_version: v2.0",
                        "snapshot_time: 2026-05-20 14:52:59+08:00",
                        "latest_anchor_trade_date: 2026-05-15",
                        "quote_trade_date: 2026-05-20",
                        "current_holding: long_microcap_short_zz1000",
                        "next_holding: long_microcap_short_zz1000",
                        "trade_state: hold",
                    ]
                ),
                encoding="utf-8",
            )

            old_argv = sys.argv
            try:
                sys.argv = [
                    "build_microcap_realtime_digest.py",
                    "--result",
                    f"v2.0={result_v20}",
                    "--out-dir",
                    str(out_dir),
                    "--planned",
                    "11:00 Asia/Shanghai",
                    "--started",
                    "2026-05-20 14:50:18 CST",
                    "--exit-code",
                    "v2.0=0",
                ]
                self.assertEqual(digest.main(), 0)
            finally:
                sys.argv = old_argv

            meta = json.loads((out_dir / "metadata.json").read_text(encoding="utf-8"))

        self.assertIn("[STALE]", meta["subject"])
        self.assertIn("Digest status: STALE", meta["body"])
        self.assertIn("v2.0 status: STALE", meta["body"])
        self.assertIn("anchor 2026-05-15 is older than expected 2026-05-19", meta["body"])
        self.assertIn("current_holding: long_microcap_short_zz1000", meta["body"])

    def test_preflight_refresh_failure_uses_refresh_reason(self) -> None:
        status, note = digest.classify_signal_output(
            "\n".join(
                [
                    "preflight_failed",
                    "refresh_exit_code: 1",
                    "reason: Top100 realtime state refresh failed, so the v2_0 realtime signal was not run.",
                    "refresh_log: microcap/realtime_state_refresh_result.txt",
                ]
            ),
            "unknown",
        )

        self.assertEqual(status, "FAILED")
        self.assertIn("state refresh failed", note)
        self.assertNotIn("marker is missing", note)


if __name__ == "__main__":
    unittest.main()
