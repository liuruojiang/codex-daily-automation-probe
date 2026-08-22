from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import etf_movers as movers  # noqa: E402


def row(symbol: str, name: str, *, price: float = 20.0, volume: int = 500_000, change: float = 1.0) -> dict[str, object]:
    return {
        "symbol": symbol,
        "longName": name,
        "regularMarketPrice": price,
        "averageDailyVolume3Month": volume,
        "regularMarketVolume": volume,
        "regularMarketChangePercent": change,
        "regularMarketTime": 1_787_266_800,
    }


class EtfMoverRulesTests(unittest.TestCase):
    def test_liquidity_requires_both_share_and_dollar_volume(self) -> None:
        self.assertTrue(movers.is_liquid(row("GOOD", "Liquid ETF")))
        self.assertFalse(movers.is_liquid(row("LOWD", "Low Dollar ETF", price=10.0, volume=100_000)))
        self.assertFalse(movers.is_liquid(row("LOWS", "Low Share ETF", price=200.0, volume=30_000)))

    def test_excludes_leveraged_inverse_bear_and_etn(self) -> None:
        self.assertEqual(movers.exclusion_reason(row("TQQQ", "ProShares UltraPro QQQ")), "leveraged_or_inverse")
        self.assertEqual(movers.exclusion_reason(row("SH", "ProShares Short S&P500")), "leveraged_or_inverse")
        self.assertEqual(movers.exclusion_reason(row("HDGE", "AdvisorShares Ranger Equity Bear ETF")), "leveraged_or_inverse")
        self.assertEqual(movers.exclusion_reason(row("RFIX", "Simplify Bond Bull ETF")), "leveraged_or_inverse")
        self.assertEqual(movers.exclusion_reason(row("VXX", "iPath S&P 500 VIX Short-Term Futures ETN")), "etn")

    def test_allows_unleveraged_futures_etfs(self) -> None:
        self.assertIsNone(movers.exclusion_reason(row("PDBC", "Invesco Optimum Yield Diversified Commodity Strategy ETF")))
        self.assertIsNone(movers.exclusion_reason(row("DBMF", "iMGP DBi Managed Futures Strategy ETF")))
        self.assertIsNone(movers.exclusion_reason(row("VIXY", "ProShares VIX Short-Term Futures ETF")))
        self.assertIsNone(movers.exclusion_reason(row("USO", "United States Oil Fund LP")))

    def test_excludes_single_crypto_and_physical_trusts(self) -> None:
        self.assertEqual(movers.exclusion_reason(row("XRPX", "Canary XRP ETF")), "single_crypto")
        self.assertEqual(movers.exclusion_reason(row("IBIT", "iShares Bitcoin Trust ETF")), "single_crypto")
        self.assertEqual(movers.exclusion_reason(row("IAU", "iShares Gold Trust")), "physical_or_spot_trust")

    def test_theme_dedupe_keeps_one_product_per_underlying_theme(self) -> None:
        records = []
        for symbol, name, change in [
            ("SMH", "VanEck Semiconductor ETF", 5.0),
            ("SOXX", "iShares Semiconductor ETF", 4.0),
            ("XBI", "SPDR S&P Biotech ETF", 3.0),
        ]:
            source = row(symbol, name, change=change)
            records.append(movers._record(source, "2026-08-21", change))
        ranked = movers._rank(records, True, 10)
        self.assertEqual([item["symbol"] for item in ranked], ["SMH", "XBI"])

    def test_theme_keys_merge_equivalent_mining_photonics_and_defense_products(self) -> None:
        self.assertEqual(movers.theme_key(row("COPP", "Sprott Copper Miners ETF")), "copper_miners")
        self.assertEqual(movers.theme_key(row("ICOP", "iShares Copper and Metals Mining ETF")), "copper_miners")
        self.assertEqual(movers.theme_key(row("SIL", "Global X Silver Miners ETF")), "silver_miners")
        self.assertEqual(movers.theme_key(row("SLVP", "iShares MSCI Global Silver and Metals Miners ETF")), "silver_miners")
        self.assertEqual(movers.theme_key(row("FOTO", "Tuttle Capital Pure Play Photonics ETF")), "photonics")
        self.assertEqual(movers.theme_key(row("LAZR", "Tema Photonics and Optical Technology ETF")), "photonics")
        self.assertEqual(movers.theme_key(row("JEDI", "Defiance Drone and Modern Warfare ETF")), "defense_aerospace")
        self.assertEqual(movers.theme_key(row("XAR", "SPDR S&P Aerospace & Defense ETF")), "defense_aerospace")


if __name__ == "__main__":
    unittest.main()
