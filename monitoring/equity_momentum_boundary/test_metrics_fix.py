"""Regression contracts for valid weekly evidence and a complete volatility window."""

import copy
import math
import unittest

import pandas as pd

from advisory import build_advisory
from monitor import SourceResult, _build_sp500_context
from test_advisory import sample
from weekly_view import market_lines, monthly_update


class WeeklyEvidenceValidationTests(unittest.TestCase):
    def pair(self):
        current = sample()
        prior = copy.deepcopy(current)
        prior["generated_at_utc"] = "2026-10-12T06:00:00+00:00"
        prior["supplemental"]["sp500_price_momentum"].update(as_of="2026-10-09", momentum_1m=.01,
                                                               realized_vol_20d_annualized=.1)
        current["supplemental"]["sp500_price_momentum"]["realized_vol_20d_annualized"] = .2
        return current, prior

    def test_healthy_comparison_preserves_numeric_values(self):
        current, prior = self.pair()
        text = "\n".join(market_lines(current, prior))
        self.assertIn("本周涨跌-1.95%", text)
        self.assertIn("较上期-0.72个百分点", text)
        self.assertIn("较上期+10.00个百分点", text)

    def test_invalid_current_observation_cannot_be_this_weeks_evidence(self):
        for as_of in ("2026-10-01", "2026-10-20", None, "invalid"):
            with self.subTest(as_of=as_of):
                current, prior = self.pair()
                current["supplemental"]["sp500_price_momentum"]["as_of"] = as_of
                advisory = build_advisory(current, prior)
                fast = "\n".join(dict(advisory["sections"])["本周市场变化（快指标）"])
                self.assertIn("证据不足", advisory["stance"])
                self.assertNotIn("本周涨跌-1.95%", fast)
                self.assertNotIn("较上期", fast)
                self.assertIn("不能作为本周有效证据", fast)

    def test_invalid_current_price_or_momentum_cannot_be_fast_evidence(self):
        for key, value in (("price", 0), ("price", float("inf")), ("momentum_1m", -1),
                           ("momentum_3m", float("nan")), ("momentum_12m", None)):
            with self.subTest(key=key, value=value):
                current, prior = self.pair()
                current["supplemental"]["sp500_price_momentum"][key] = value
                text = "\n".join(market_lines(current, prior))
                self.assertNotIn("本周涨跌-1.95%", text)
                self.assertNotIn("较上期", text)

    def test_vix_validity_matches_the_conclusion_gate(self):
        for changes in ({"weekly_change": 20.99}, {"weekly_change": float("nan")}, {"value": 0},
                        {"one_year_percentile": 1.01}, {"as_of": "2026-10-15"},
                        {"as_of": "2026-10-01"}, {"as_of": "2026-10-20"}, {"as_of": None}):
            with self.subTest(changes=changes):
                current, prior = self.pair()
                current["supplemental"]["vix"].update(changes)
                advisory = build_advisory(current, prior)
                text = "\n".join(dict(advisory["sections"])["本周市场变化（快指标）"])
                self.assertIn("波动证据缺失、异常或日期不齐", advisory["conclusion"])
                self.assertIn("不作为本周有效波动证据", text)
                self.assertNotIn("VIX截至", text)

    def test_prior_cutoff_must_be_fresh_and_not_after_its_report(self):
        for as_of in ("2026-10-13", "2026-10-01", None, "invalid"):
            with self.subTest(as_of=as_of):
                current, prior = self.pair()
                prior["supplemental"]["sp500_price_momentum"]["as_of"] = as_of
                text = "\n".join(market_lines(current, prior))
                self.assertIn("本周涨跌-1.95%", text)
                self.assertIn("上期不可比或缺失", text)
                self.assertNotIn("较上期", text)

    def test_exact_seven_day_age_is_allowed_but_eighth_day_is_not(self):
        current, prior = self.pair()
        prior["supplemental"]["sp500_price_momentum"]["as_of"] = "2026-10-05"
        self.assertIn("较上期", "\n".join(market_lines(current, prior)))
        prior["supplemental"]["sp500_price_momentum"]["as_of"] = "2026-10-04"
        self.assertNotIn("较上期", "\n".join(market_lines(current, prior)))


class CompleteVolatilityWindowTests(unittest.TestCase):
    def series(self, count):
        return pd.Series([100 + i + i % 3 for i in range(count)],
                         index=pd.bdate_range("2026-09-01", periods=count), dtype=float)

    def context(self, series):
        source = SourceResult("synthetic regression fixture", "local-test", str(series.index[-1].date()), "ok")
        return _build_sp500_context(series, source)

    def test_twenty_prices_only_have_nineteen_returns(self):
        self.assertIsNone(self.context(self.series(20))["realized_vol_20d_annualized"])

    def test_twenty_one_prices_match_the_unchanged_full_window_formula(self):
        series = self.series(21)
        expected = (series / series.shift()).apply(lambda x: math.log(x) if x > 0 else float("nan")).tail(20).std(ddof=1) * math.sqrt(252)
        self.assertEqual(self.context(series)["realized_vol_20d_annualized"], expected)

    def test_nonfinite_or_nonpositive_prices_in_tail_do_not_shrink_the_window(self):
        for value in (float("nan"), float("inf"), 0, -1):
            for position in (-21, -11, -2):
                with self.subTest(value=value, position=position):
                    series = self.series(40)
                    series.iloc[position] = value
                    self.assertIsNone(self.context(series)["realized_vol_20d_annualized"])

    def test_bad_price_before_full_window_does_not_invalidate_recovered_tail(self):
        series = self.series(40)
        expected = self.context(series)["realized_vol_20d_annualized"]
        series.iloc[-22] = float("nan")
        self.assertEqual(self.context(series)["realized_vol_20d_annualized"], expected)


if __name__ == "__main__":
    unittest.main()
