"""Adversarial regressions for calendar integrity and fallback rate aggregation."""
import unittest
from unittest.mock import patch
import pandas as pd
import monitor


class DataAuditTests(unittest.TestCase):
    def test_shiller_numeric_october_keeps_all_twelve_months(self):
        dates = pd.Series([float(f"2025.{m:02d}") for m in range(1, 13)])
        periods = monitor._parse_shiller_periods(dates)
        self.assertEqual(periods.tolist(), pd.period_range("2025-01", "2025-12", freq="M").tolist())
        self.assertEqual(monitor._parse_shiller_periods(pd.Series(["2026.10", "2026.1", "2026.01"])).astype(str).tolist(),
                         ["2026-10", "2026-10", "2026-01"])

    def test_shiller_load_cannot_overwrite_january_with_october(self):
        frame = pd.DataFrame({"Date": [float(f"2025.{m:02d}") for m in range(1, 13)],
                              "P": range(101, 113), "D": [2] * 12, "CAPE": range(21, 33)})
        with patch.object(monitor, "discover_shiller_url", return_value="https://example.org"), \
             patch.object(monitor, "fetch_bytes", return_value=b"xls"), \
             patch.object(pd, "read_excel", return_value=frame):
            result, _ = monitor.load_shiller_data()
        self.assertEqual(len(result), 12)
        self.assertEqual(result.loc["2025-01", "cape"], 21)
        self.assertEqual(result.loc["2025-10", "cape"], 30)

    def test_missing_month_is_not_compressed_into_ma12(self):
        idx = pd.period_range("2000-01", periods=400, freq="M")
        values = pd.Series(range(400), index=idx, dtype=float).drop(idx[-5])
        result = monitor.boundary_component(values, 120)
        self.assertTrue(pd.isna(result.loc[idx[-5], "raw"]))
        self.assertTrue(pd.isna(result.iloc[-1]["ma12"]))
        self.assertTrue(pd.isna(result.iloc[-1]["extreme"]))
        self.assertTrue(pd.isna(result.iloc[-1]["score"]))

    def test_missing_month_breaks_rolling_history_even_after_ma_recovers(self):
        idx = pd.period_range("2000-01", periods=400, freq="M")
        values = pd.Series(range(400), index=idx, dtype=float).drop(idx[-50])
        result = monitor.boundary_component(values, 120)
        self.assertFalse(pd.isna(result.iloc[-1]["ma12"]))
        self.assertTrue(pd.isna(result.iloc[-1]["percentile"]))
        self.assertTrue(pd.isna(result.iloc[-1]["extreme"]))

    def test_unknown_and_degenerate_windows_do_not_claim_non_extreme(self):
        idx = pd.period_range("2000-01", periods=300, freq="M")
        constant = monitor.boundary_component(pd.Series(1., index=idx), 120)
        self.assertTrue(pd.isna(constant.iloc[-1]["extreme"]))
        self.assertTrue(pd.isna(constant.iloc[-1]["score"]))
        self.assertTrue(pd.isna(constant.iloc[0]["extreme"]))
        varying = monitor.boundary_component(pd.Series(range(300), index=idx, dtype=float), 120)
        self.assertEqual(varying["score"].first_valid_index(), idx[130])
        self.assertTrue(varying.iloc[-1]["extreme"])

    def test_duplicate_months_fail_loudly(self):
        series = pd.Series([1., 2.], index=pd.PeriodIndex(["2025-01", "2025-01"], freq="M"))
        with self.assertRaisesRegex(ValueError, "unique"):
            monitor.boundary_component(series, 120)

    def test_h15_fallback_monthly_averages_and_discloses_basis_difference(self):
        rates = pd.DataFrame({"ten_year": [4., 8.], "three_month": [2., 2.], "two_year": [3., 5.]},
                             index=pd.to_datetime(["2026-08-03", "2026-08-31"]))
        with patch.object(monitor, "load_fred_series_bulk", side_effect=RuntimeError("offline")), \
             patch.object(monitor, "load_h15_treasury_rates", return_value=(rates, [])):
            result, _, warnings = monitor.build_term_spread_proxy()
        self.assertEqual(result.iloc[-1]["term_spread"], 4.)
        self.assertEqual(result.iloc[-1]["term_spread_10y2y"], 2.)
        self.assertTrue(any("discount-rate" in text for text in warnings))


if __name__ == "__main__":
    unittest.main()
