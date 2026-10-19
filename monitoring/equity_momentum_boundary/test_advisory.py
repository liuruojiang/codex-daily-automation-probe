import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from advisory import VARIANTS, build_advisory, render_advisory_html, render_advisory_text
from monitor import main, render_html, render_text


def sample():
    core = {key: dict(status="ok", window_years=10 if "10y" in key else 20,
                     as_of="2026-09", valuation_percentile=.95 if key.startswith("cape") else .05,
                     term_spread_percentile=.55, valuation_ma12=39.4,
                     term_spread_ma12=.6, boundary_score_0_to_2=1.02,
                     extreme_components=1, state="elevated") for key in VARIANTS}
    return dict(monitor="Weekly Equity Momentum Boundary Monitor", generated_at_utc="2026-10-19T06:00:00+00:00",
                overall_state="偏边界", core=core, sources=[], warnings=[], supplemental={
                    "sp500_price_momentum": dict(as_of="2026-10-16", price=7659.44, weekly_change=-152.1,
                        momentum_1m=.0028, momentum_3m=.029, momentum_12m=.1482),
                    "vix": dict(as_of="2026-10-16", value=19.99, weekly_change=5.15, one_year_percentile=.8214)})


class AdvisoryTests(unittest.TestCase):
    def test_positive_trend_with_rising_vix_raises_caution(self):
        result = build_advisory(sample())
        self.assertEqual(result["stance"], "趋势仍正，风险警戒升温")
        text = render_advisory_text(result)
        self.assertIn("-1.95%", text)
        self.assertIn("偏高估（低股息率）", text)
        self.assertIn("信用利差缺失", text)

    def test_positive_trend_without_stress_is_not_a_sell_signal(self):
        report = sample()
        report["supplemental"]["vix"]["one_year_percentile"] = .4
        result = build_advisory(report)
        self.assertEqual(result["stance"], "趋势仍正，留意极端环境")
        self.assertIn("不能单独推出立即卖出", render_advisory_text(result))

    def test_short_reversal_and_negative_long_trend_are_distinct(self):
        report = sample()
        report["supplemental"]["sp500_price_momentum"]["momentum_1m"] = -.02
        self.assertEqual(build_advisory(report)["stance"], "趋势受扰，放缓新增风险")
        report["supplemental"]["sp500_price_momentum"]["momentum_12m"] = -.01
        self.assertEqual(build_advisory(report)["stance"], "偏防守，核查策略退出条件")

    def test_missing_or_stale_data_blocks_confident_advice(self):
        for change in (lambda r: r["core"].pop(VARIANTS[0]),
                       lambda r: r["supplemental"]["sp500_price_momentum"].update(as_of="2026-09-01"),
                       lambda r: r["supplemental"]["sp500_price_momentum"].update(momentum_12m=None)):
            report = sample()
            change(report)
            self.assertEqual(build_advisory(report)["stance"], "证据不足，暂缓新增风险判断")

    def test_reference_month_update_is_not_weekly_market_move(self):
        report = sample()
        previous = copy.deepcopy(report)
        previous["generated_at_utc"] = "2026-10-12T06:00:00+00:00"
        previous["core"]["dividend_yield_boundary_10y"]["as_of"] = "2025-12"
        report["core"]["dividend_yield_boundary_10y"]["as_of"] = "2026-06"
        previous["core"]["cape_boundary_10y"]["valuation_ma12"] = 39.5
        text = render_advisory_text(build_advisory(report, previous))
        self.assertIn("属于参考期更新", text)
        self.assertIn("可能为历史修订", text)
        self.assertIn("较旧股息率仅作滞后背景", text)

    def test_stale_vix_cannot_trigger_current_volatility_warning(self):
        report = sample()
        report["supplemental"]["vix"]["as_of"] = "2026-09-01"
        self.assertEqual(build_advisory(report)["stance"], "趋势仍正，留意极端环境")

    def test_stale_core_blocks_composite_conclusion(self):
        report = sample()
        report["core"]["cape_boundary_10y"]["as_of"] = "2025-01"
        self.assertEqual(build_advisory(report)["stance"], "证据不足，暂缓新增风险判断")

    def test_html_escapes_dynamic_content_and_both_formats_lead_with_conclusion(self):
        report = sample()
        report["core"][VARIANTS[0]]["as_of"] = "<script>"
        self.assertIn("&lt;script&gt;", render_advisory_html(build_advisory(report)))
        self.assertNotIn("<script>", render_html(report))
        for renderer in (render_text, render_html):
            result = renderer(report)
            self.assertLess(result.index("本周结论"), result.index("核心论文式指标"))

    def test_cli_writes_advisory_into_json_text_html_and_metadata(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp, patch("monitor.build_report", return_value=sample()):
            with patch("builtins.print"):
                self.assertEqual(main(["--output-dir", temp]), 0)
            report = json.loads((Path(temp) / "report.json").read_text(encoding="utf-8"))
            metadata = json.loads((Path(temp) / "metadata.json").read_text(encoding="utf-8"))
            self.assertIn("advisory", report)
            self.assertIn("可参考的应对建议", metadata["body"])
            self.assertIn("下周观察", metadata["html_body"])

    def test_cli_rejects_future_and_corrupt_snapshots(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
            prior = Path(temp) / "prior.json"
            for data in ("{broken", json.dumps(dict(monitor=sample()["monitor"], generated_at_utc="2027-01-01T00:00:00+00:00"))):
                prior.write_text(data, encoding="utf-8")
                with patch("monitor.build_report", return_value=sample()), patch("builtins.print"):
                    main(["--output-dir", temp, "--previous-report", str(prior)])
                output = json.loads((Path(temp) / "report.json").read_text(encoding="utf-8"))
                self.assertTrue(output["warnings"])
                self.assertIn("没有有效的上期报告", render_advisory_text(output["advisory"]))


if __name__ == "__main__":
    unittest.main()
