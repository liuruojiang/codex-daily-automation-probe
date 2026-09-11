import unittest
from unittest.mock import patch

import pandas as pd

from monitor import boundary_component, load_fred_series_bulk, scale_to_signed


class BoundaryMathTests(unittest.TestCase):
    def test_min_mid_max_map_to_signed_unit_interval(self) -> None:
        self.assertEqual(scale_to_signed(0.0, 0.0, 10.0), -1.0)
        self.assertEqual(scale_to_signed(5.0, 0.0, 10.0), 0.0)
        self.assertEqual(scale_to_signed(10.0, 0.0, 10.0), 1.0)

    def test_both_ends_have_same_squared_boundary_contribution(self) -> None:
        self.assertEqual(scale_to_signed(0.0, 0.0, 10.0) ** 2, scale_to_signed(10.0, 0.0, 10.0) ** 2)

    def test_rolling_component_requires_twelve_month_average_and_history(self) -> None:
        index = pd.period_range("2000-01", periods=130, freq="M")
        values = pd.Series(range(len(index)), index=index, dtype=float)
        result = boundary_component(values, 120)
        self.assertTrue(result["score"].isna().all())

        index = pd.period_range("2000-01", periods=250, freq="M")
        values = pd.Series(range(len(index)), index=index, dtype=float)
        result = boundary_component(values, 240)
        self.assertTrue(result["score"].isna().iloc[-1])

    @patch("monitor.fetch_bytes")
    def test_bulk_fred_csv_parser_keeps_each_series_and_date(self, fetch_bytes) -> None:
        fetch_bytes.return_value = (
            b"observation_date,GS10,TB3MS\n"
            b"2026-07-01,4.60,4.35\n"
            b"2026-08-01,4.68,4.30\n"
        )
        result = load_fred_series_bulk(["GS10", "TB3MS"])
        self.assertEqual(result["GS10"][0].iloc[-1], 4.68)
        self.assertEqual(result["TB3MS"][0].index[-1].strftime("%Y-%m"), "2026-08")
        self.assertIn("GS10,TB3MS", result["GS10"][1].detail)


if __name__ == "__main__":
    unittest.main()
