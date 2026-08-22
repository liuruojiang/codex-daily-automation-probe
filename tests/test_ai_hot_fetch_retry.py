from __future__ import annotations

import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import daily_reports as dr  # noqa: E402


class AiHotFetchRetryTests(unittest.TestCase):
    def test_build_ai_retries_transient_fetch_failures_before_succeeding(self) -> None:
        attempts: list[str] = []
        original_fetch_bytes = dr.fetch_bytes
        original_sleep = dr.time.sleep
        original_now_bj = dr.now_bj

        payload = {
            "date": "2026-07-05",
            "lead": "今日 AI 热点以模型更新为主。",
            "sections": [
                {
                    "label": "模型发布/更新",
                    "items": [
                        {
                            "title": "Example model update",
                            "summary": "A model update shipped.",
                            "sourceName": "Example",
                            "sourceUrl": "https://example.com/model",
                        }
                    ],
                }
            ],
        }

        def flaky_fetch(url: str, timeout: int = 30, headers: dict[str, str] | None = None) -> bytes:
            attempts.append(url)
            if len(attempts) < 3:
                raise urllib.error.URLError("timed out")
            return json.dumps(payload).encode("utf-8")

        try:
            dr.fetch_bytes = flaky_fetch
            dr.time.sleep = lambda _seconds: None
            dr.now_bj = lambda: dr.datetime(2026, 7, 5, 12, 20, tzinfo=dr.BJ)
            with tempfile.TemporaryDirectory() as tmp:
                out_dir = Path(tmp)
                dr.build_ai(out_dir)
                metadata = json.loads((out_dir / "metadata.json").read_text(encoding="utf-8"))
        finally:
            dr.fetch_bytes = original_fetch_bytes
            dr.time.sleep = original_sleep
            dr.now_bj = original_now_bj

        self.assertEqual(len(attempts), 3)
        self.assertEqual(metadata["subject"], "AI HOT 日报 - 2026-07-05")
        self.assertIsNone(metadata["attachment"])
        self.assertIn("<!doctype html>", metadata["html_body"].lower())
        self.assertIn("Example model update", metadata["html_body"])
        self.assertIn("Example model update", metadata["body"])
        self.assertFalse(any(out_dir.glob("*.md")))

    def test_build_ai_reports_attempt_count_when_fetch_never_recovers(self) -> None:
        original_fetch_bytes = dr.fetch_bytes
        original_sleep = dr.time.sleep

        def always_fails(url: str, timeout: int = 30, headers: dict[str, str] | None = None) -> bytes:
            raise urllib.error.URLError("connection refused")

        try:
            dr.fetch_bytes = always_fails
            dr.time.sleep = lambda _seconds: None
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(RuntimeError, "after 4 attempts"):
                    dr.build_ai(Path(tmp))
        finally:
            dr.fetch_bytes = original_fetch_bytes
            dr.time.sleep = original_sleep


if __name__ == "__main__":
    unittest.main()
