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


    def test_quiet_day_renders_real_source_lead_and_explicit_zero_items(self) -> None:
        raw = (ROOT / "tests/fixtures/ai_hot/2026-11-16.json").read_bytes()
        report = json.loads(raw)["report"]
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(dr, "fetch_bytes", return_value=raw):
                dr.build_ai(Path(tmp), requested_date=report["date"])
            metadata = json.loads((Path(tmp) / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["subject"], "AI HOT 日报 - 2026-11-16")
        for body in (metadata["body"], metadata["html_body"]):
            self.assertIn(report["lead"]["leadParagraph"], body)
            self.assertIn("本期来源未列出新闻或快讯（0 条）", body)
            self.assertNotIn("模型、产品和行业动态为主", body)
        self.assertIsNone(metadata["attachment"])

    def test_flash_only_report_is_not_rejected_for_empty_sections(self) -> None:
        payload = json.loads((ROOT / "tests/fixtures/ai_hot/2026-11-01.json").read_bytes())
        payload["report"]["sections"] = []
        payload["report"]["lead"] = None
        self.assertTrue(payload["report"]["flashes"])
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(dr, "fetch_bytes", return_value=json.dumps(payload).encode("utf-8")):
                dr.build_ai(Path(tmp), requested_date="2026-11-01")
            metadata = json.loads((Path(tmp) / "metadata.json").read_text(encoding="utf-8"))
        self.assertIn(payload["report"]["flashes"][0]["title"], metadata["body"])
        self.assertIn("快讯", metadata["html_body"])
        self.assertNotIn("本期来源未列出新闻或快讯", metadata["body"])

    def test_empty_or_malformed_reports_cannot_produce_sendable_metadata(self) -> None:
        valid = json.loads((ROOT / "tests/fixtures/ai_hot/2026-11-16.json").read_bytes())
        invalid_reports = [
            {key: value for key, value in valid["report"].items() if key != "sections"},
            {**valid["report"], "sections": None},
            {**valid["report"], "sections": {}},
            {**valid["report"], "flashes": {}},
            {**valid["report"], "lead": None},
            {**valid["report"], "lead": {"title": " ", "leadParagraph": " "}},
            {**valid["report"], "lead": None, "sections": [{"label": "模型", "items": []}]},
        ]
        for report in invalid_reports:
            with self.subTest(report=report), tempfile.TemporaryDirectory() as tmp:
                raw = json.dumps({"schemaVersion": 1, "report": report}).encode("utf-8")
                with patch.object(dr, "fetch_bytes", return_value=raw):
                    with self.assertRaises(ValueError):
                        dr.build_ai(Path(tmp), requested_date="2026-11-16")
                self.assertFalse((Path(tmp) / "metadata.json").exists())

    def test_gzip_response_is_decoded_before_json_parsing(self) -> None:
        response = io.BytesIO(gzip.compress(b'{"schemaVersion":1}'))
        response.headers = {"Content-Encoding": "gzip"}
        with patch.object(dr.urllib.request, "urlopen", return_value=response):
            self.assertEqual(dr.fetch_bytes("https://aihot.news/api/v1/dailies/latest"), b'{"schemaVersion":1}')

    def test_counts_include_news_and_flashes_in_every_summary(self) -> None:
        original = json.loads((ROOT / "tests/fixtures/ai_hot/2026-11-01.json").read_bytes())
        for case in ("mixed", "news_only", "flash_only", "flash_with_empty_section"):
            payload = json.loads(json.dumps(original))
            report = payload["report"]
            if case == "news_only":
                report["flashes"] = []
            elif case == "flash_only":
                report["sections"] = []
            elif case == "flash_with_empty_section":
                report["sections"] = [{"label": "模型", "items": []}]
            news_count = sum(len(section["items"]) for section in report["sections"])
            flash_count = len(report["flashes"])
            total_count = news_count + flash_count
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                with patch.object(dr, "fetch_bytes", return_value=json.dumps(payload).encode("utf-8")):
                    dr.build_ai(Path(tmp), requested_date=report["date"])
                metadata = json.loads((Path(tmp) / "metadata.json").read_text(encoding="utf-8"))
            self.assertIn(f"共 {total_count} 条：", metadata["body"])
            self.assertIn(f"{report['date']} · 共 {total_count} 条", metadata["html_body"])
            self.assertIn(f"今日 {total_count} 条 AI 动态", metadata["html_body"])
            if flash_count:
                for body in (metadata["body"], metadata["html_body"]):
                    self.assertIn(f"快讯 {flash_count} 条", body)

    def test_unfinished_or_ambiguous_empty_reports_cannot_create_metadata(self) -> None:
        original = json.loads((ROOT / "tests/fixtures/ai_hot/2026-11-16.json").read_bytes())
        variants = [
            {"lead": {"title": "日报生成中", "leadParagraph": "新闻内容稍后更新。"}},
            {"lead": "新闻内容稍后更新。"},
            {"lead": {"title": "今日 AI 热点", "leadParagraph": "今天发布多个新模型。"}},
            {"lead": {"title": "今日安静，无大事发生", "leadParagraph": "新闻内容稍后更新。"}},
            {"lead": {"title": "日报生成中", "leadParagraph": original["report"]["lead"]["leadParagraph"]}},
            {"lead": {"title": "今日安静，无大事发生"}},
            {"lead": {"title": "今日安静，无大事发生", "leadParagraph": "北京时间 11 月 14 日 08:00 至 11 月 15 日 08:00，没有新的 AI 大事。"}},
            {"windowStart": "2026-11-14T00:00:00Z"},
            {"windowEnd": "2026-11-17T00:00:00Z"},
            {"windowStart": None},
            {"windowEnd": "not-a-timestamp"},
            {"generatedAt": "2026-11-15T23:59:59Z"},
            {"generatedAt": "2026-11-16T08:00:43"},
            {"generatedAt": None},
            {"flashes": None},
            {"highlights": [{"title": "尚有待处理的看点"}]},
            {"sections": [{"label": "模型", "items": []}]},
        ]
        for changes in variants:
            payload = json.loads(json.dumps(original))
            payload["report"].update(changes)
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as tmp:
                raw = json.dumps(payload).encode("utf-8")
                with patch.object(dr, "fetch_bytes", return_value=raw):
                    with self.assertRaises(ValueError):
                        dr.build_ai(Path(tmp), requested_date="2026-11-16")
                self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_quiet_day_coverage_handles_year_boundary_and_timezone_offsets(self) -> None:
        payload = json.loads((ROOT / "tests/fixtures/ai_hot/2026-11-16.json").read_bytes())
        payload["report"].update({
            "date": "2027-01-01",
            "windowStart": "2026-12-31T08:00:00+08:00",
            "windowEnd": "2027-01-01T08:00:00+08:00",
            "generatedAt": "2027-01-01T08:00:43+08:00",
            "lead": {
                "title": "今日安静，无大事发生",
                "leadParagraph": "北京时间 12 月 31 日 08:00 至 1 月 1 日 08:00，没有新的 AI 大事。",
            },
        })
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(dr, "fetch_bytes", return_value=json.dumps(payload).encode("utf-8")):
                dr.build_ai(Path(tmp), requested_date="2027-01-01")
            metadata = json.loads((Path(tmp) / "metadata.json").read_text(encoding="utf-8"))
        self.assertIn("共 0 条：", metadata["body"])
        self.assertIn("本期来源未列出新闻或快讯（0 条）", metadata["html_body"])

    def test_real_quiet_day_passes_main_and_validation_only_delivery_gate(self) -> None:
        import check_daily_digest_delivery as delivery
        raw = (ROOT / "tests/fixtures/ai_hot/2026-11-16.json").read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            with patch.object(dr, "fetch_bytes", return_value=raw), patch.object(
                sys, "argv", ["daily_reports.py", "ai-hot", "--report-date", "2026-11-16", "--out-dir", tmp]
            ):
                self.assertEqual(dr.main(), 0)
            output = io.StringIO()
            with patch.object(sys, "argv", [
                "check_daily_digest_delivery.py", "--family", "ai-hot",
                "--metadata", str(out_dir / "metadata.json"), "--validation-only",
            ]), patch.dict(delivery.os.environ, {"GITHUB_OUTPUT": ""}), patch("sys.stdout", output), patch.object(
                delivery.GitHub, "get", side_effect=AssertionError("validation must not access delivery history")
            ):
                self.assertEqual(delivery.main(), 0)
            self.assertIn("should_send=false", output.getvalue())
            self.assertIn("delivery_state=validation_only", output.getvalue())
            self.assertEqual(sorted(path.name for path in out_dir.iterdir()), ["metadata.json"])


if __name__ == "__main__":
    unittest.main()

