from __future__ import annotations

import json
import sys
import tempfile
import unittest
import gzip
import io
from unittest.mock import patch
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
                            "source": {"name": "Example"},
                            "links": {"original": "https://example.com/model"},
                        }
                    ],
                }
            ],
        }

        def flaky_fetch(url: str, timeout: int = 30, headers: dict[str, str] | None = None) -> bytes:
            attempts.append(url)
            if len(attempts) < 3:
                raise urllib.error.URLError("timed out")
            return json.dumps({"schemaVersion": 1, "report": payload}).encode("utf-8")

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
        self.assertEqual(attempts[-1], "https://aihot.news/api/v1/dailies/latest")
        self.assertIn("https://example.com/model", metadata["html_body"])
        self.assertIn("来源：Example", metadata["body"])
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


    def test_backfill_preserves_each_archived_reports_sources_and_links(self) -> None:
        for day in ("2026-11-01", "2026-11-02"):
            raw = (ROOT / "tests/fixtures/ai_hot" / f"{day}.json").read_bytes()
            report = json.loads(raw)["report"]
            with self.subTest(day=day), tempfile.TemporaryDirectory() as tmp:
                with patch.object(dr, "fetch_bytes", return_value=raw) as fetch:
                    dr.build_ai(Path(tmp), requested_date=day)
                self.assertEqual(fetch.call_args.args[0], f"https://aihot.news/api/v1/dailies/{day}")
                metadata = json.loads((Path(tmp) / "metadata.json").read_text(encoding="utf-8"))
                self.assertEqual(metadata["subject"], f"AI HOT 日报 - {day}")
                for item in [item for section in report["sections"] for item in section["items"]] + report["flashes"]:
                    self.assertIn(item["title"], metadata["body"])
                    self.assertIn(item["source"]["name"], metadata["body"])
                    self.assertIn(item["links"]["original"], metadata["body"])
                    self.assertIn(dr.html.escape(item["links"]["original"], quote=True), metadata["html_body"])
                self.assertNotIn("{'name':", metadata["body"])
                self.assertIsNone(metadata["attachment"])

    def test_wrong_date_or_missing_report_cannot_produce_sendable_metadata(self) -> None:
        raw = (ROOT / "tests/fixtures/ai_hot/2026-11-01.json").read_bytes()
        for payload in (raw, b'{"schemaVersion":1}', b'{"schemaVersion":2,"report":{}}'):
            with self.subTest(payload=payload[:60]), tempfile.TemporaryDirectory() as tmp:
                with patch.object(dr, "fetch_bytes", return_value=payload):
                    with self.assertRaises(ValueError):
                        dr.build_ai(Path(tmp), requested_date="2026-11-02")
                self.assertFalse((Path(tmp) / "metadata.json").exists())
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(dr, "fetch_bytes", return_value=raw), patch.object(
                dr, "now_bj", return_value=dr.datetime(2026, 11, 2, 9, tzinfo=dr.BJ)
            ):
                with self.assertRaisesRegex(ValueError, "date mismatch"):
                    dr.build_ai(Path(tmp))

    def test_gzip_response_is_decoded_before_json_parsing(self) -> None:
        response = io.BytesIO(gzip.compress(b'{"schemaVersion":1}'))
        response.headers = {"Content-Encoding": "gzip"}
        with patch.object(dr.urllib.request, "urlopen", return_value=response):
            self.assertEqual(dr.fetch_bytes("https://aihot.news/api/v1/dailies/latest"), b'{"schemaVersion":1}')


if __name__ == "__main__":
    unittest.main()
