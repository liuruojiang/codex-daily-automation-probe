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

                old_argv = sys.argv
                try:
                    sys.argv = [
                        "build_microcap_realtime_digest.py",
                        "--result",
                        f"v2.0={result_v20}",
                        "--result",
                        f"v2.3={result_v23}",
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
        self.assertIn("microcap_mom: +12.3400%", v20_summary)
        self.assertIn("hedge_mom: +4.5600%", v20_summary)
        self.assertIn("momentum_gap: +7.7800%", v20_summary)
        self.assertNotIn("见附件", meta["body"])
        self.assertFalse(meta.get("attachment"))


if __name__ == "__main__":
    unittest.main()
