from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
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
            mock.patch.object(dr.broad_etf_movers, "fetch_universe", return_value=[]),
            mock.patch.object(
                dr.broad_etf_movers,
                "daily_rankings",
                return_value=SimpleNamespace(gainers=[], losers=[], universe_count=0, eligible_count=0),
            ),
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

    def test_sunday_and_monday_emails_skip_market_fetches_but_keep_source_digest(self) -> None:
        for fixed_now in (
            dr.datetime(2026, 8, 23, 5, 0, tzinfo=dr.BJ),  # Sunday
            dr.datetime(2026, 8, 24, 5, 0, tzinfo=dr.BJ),  # Monday
        ):
            with self.subTest(report_date=fixed_now.date().isoformat()):
                with (
                    mock.patch.object(dr, "now_bj", return_value=fixed_now),
                    mock.patch.object(dr, "fetch_asset_changes") as fetch_asset_changes,
                    mock.patch.object(dr.broad_etf_movers, "fetch_universe") as fetch_universe,
                    mock.patch.object(dr.broad_etf_movers, "daily_rankings") as daily_rankings,
                    mock.patch.object(dr.broad_etf_movers, "period_rankings") as period_rankings,
                    mock.patch.object(dr, "parse_feed", return_value=[]) as parse_feed,
                    mock.patch.object(
                        dr, "collect_etf_fixed_monitor_updates_with_audit", return_value=([], [])
                    ) as collect_fixed_monitors,
                    mock.patch.object(dr, "collect_etf_forum_items", return_value=[]),
                    mock.patch.object(dr, "update_digest_history", return_value=None),
                    mock.patch.object(dr.time, "sleep", return_value=None),
                    tempfile.TemporaryDirectory() as tmp,
                ):
                    out_dir = Path(tmp)
                    dr.build_etf(out_dir)
                    report = (out_dir / f"us_etf_allocation_digest_{fixed_now.date().isoformat()}.md").read_text(
                        encoding="utf-8"
                    )
                    metadata = json.loads((out_dir / "metadata.json").read_text(encoding="utf-8"))

                fetch_asset_changes.assert_not_called()
                fetch_universe.assert_not_called()
                daily_rankings.assert_not_called()
                period_rankings.assert_not_called()
                self.assertEqual(parse_feed.call_count, len(dr.ETF_RESEARCH_FEEDS))
                collect_fixed_monitors.assert_called_once()
                self.assertIn("周末资讯版", report)
                self.assertIn("## 资产配置影响", report)
                self.assertIn("## RSS / 研究来源覆盖审计", report)
                self.assertIn("## 固定关注博客/播客更新", report)
                self.assertNotIn("## 策略相关 ETF / 指数涨跌", report)
                self.assertNotIn("## ETF 涨跌幅榜", report)
                self.assertNotIn("## 市场 regime 是否变化", report)
                self.assertIn("资讯源与来源审计正常运行", report)
                self.assertIsNone(metadata["attachment"])
                self.assertIn("周末资讯版", metadata["html_body"])


if __name__ == "__main__":
    unittest.main()
