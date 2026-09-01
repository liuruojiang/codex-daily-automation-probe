from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import daily_reports as dr  # noqa: E402


class EtfHtmlEmailTests(unittest.TestCase):
    def test_markdown_renderer_builds_email_safe_headings_tables_and_links(self) -> None:
        markdown = """# 美股 ETF 与资产配置日报 - 2026-08-22

## 昨日市场与 ETF 表现

| ETF | 涨跌 | 原文 |
|---|---:|---|
| QQQM | +1.20% | https://example.com/qqqm |

- **数据口径**：价格涨跌，不含分红再投资。
- 转义检查：<script>alert('x')</script>
"""
        rendered = dr.markdown_to_email_html(markdown, "完整报告直接在邮件正文查看。")

        self.assertIn("<!doctype html>", rendered.lower())
        self.assertIn("<table", rendered)
        self.assertIn('href="https://example.com/qqqm"', rendered)
        self.assertIn("<strong>数据口径</strong>", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)

    def test_build_etf_writes_full_inline_bodies_without_email_attachment(self) -> None:
        fixed_now = dr.datetime(2026, 8, 19, 8, 0, tzinfo=dr.BJ)
        patches = [
            mock.patch.object(dr, "now_bj", return_value=fixed_now),
            mock.patch.object(dr, "fetch_asset_changes", return_value=[]),
            mock.patch.object(dr, "parse_feed", return_value=[]),
            mock.patch.object(dr, "collect_etf_fixed_monitor_updates_with_audit", return_value=([], [])),
            mock.patch.object(dr, "collect_etf_forum_items", return_value=[]),
            mock.patch.object(dr, "supplement_forum_items_with_same_day_new_history", return_value=[]),
            mock.patch.object(dr, "append_etf_research_sections", return_value=0),
            mock.patch.object(dr, "update_digest_history", return_value=None),
            mock.patch.object(dr.time, "sleep", return_value=None),
        ]

        with ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            with tempfile.TemporaryDirectory() as tmp:
                out_dir = Path(tmp)
                dr.build_etf(out_dir)
                metadata = json.loads((out_dir / "metadata.json").read_text(encoding="utf-8"))
                markdown_report = (out_dir / "us_etf_allocation_digest_2026-08-19.md").read_text(encoding="utf-8")

        self.assertEqual(metadata["subject"], "美股 ETF 与资产配置日报 - 2026-08-19")
        self.assertIsNone(metadata["attachment"])
        self.assertEqual(metadata["body"], markdown_report)
        self.assertIn("<!doctype html>", metadata["html_body"].lower())
        self.assertIn("策略相关 ETF / 指数涨跌", metadata["html_body"])
        self.assertNotIn("完整排版版见附件", metadata["body"])


if __name__ == "__main__":
    unittest.main()
