from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import etf_movers as movers  # noqa: E402


def row(
    symbol: str,
    name: str,
    *,
    price: float = 20.0,
    volume: int = 500_000,
    change: float = 1.0,
    assets: float = 100_000_000.0,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "longName": name,
        "regularMarketPrice": price,
        "averageDailyVolume3Month": volume,
        "regularMarketVolume": volume,
        "regularMarketChangePercent": change,
        "regularMarketTime": 1_787_266_800,
        "netAssets": assets,
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
        self.assertEqual(movers.exclusion_reason(row("GLDM", "SPDR Gold MiniShares")), "physical_or_spot_trust")
        self.assertEqual(movers.exclusion_reason(row("BLOX", "Nicholas Crypto Income ETF")), "option_income_or_defined_outcome")

    def test_theme_dedupe_keeps_one_product_per_underlying_theme(self) -> None:
        records = []
        for symbol, name, change, volume in [
            ("SMH", "VanEck Semiconductor ETF", 5.0, 1_000_000),
            ("SOXX", "iShares Semiconductor ETF", 4.0, 500_000),
            ("XBI", "SPDR S&P Biotech ETF", 3.0, 500_000),
        ]:
            source = row(symbol, name, change=change, volume=volume)
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

    def test_broad_resource_equity_family_keeps_only_one_mining_theme(self) -> None:
        records = []
        for symbol, name, change in [
            ("URNJ", "Sprott Junior Uranium Miners ETF", 8.0),
            ("REMX", "VanEck Rare Earth and Strategic Metals ETF", 7.0),
            ("COPP", "Sprott Copper Miners ETF", 6.0),
            ("SETM", "Sprott Critical Materials ETF", 5.0),
            ("RING", "iShares MSCI Global Gold Miners ETF", 4.0),
            ("VNM", "VanEck Vietnam ETF", 3.0),
        ]:
            records.append(movers._record(row(symbol, name, change=change), "2026-08-21", change))
        ranked = movers._rank(records, True, 10)
        self.assertEqual([item["symbol"] for item in ranked], ["URNJ", "VNM"])

    def test_visible_name_and_description_are_useful_chinese_copy(self) -> None:
        record = movers._record(row("NCLD", "Roundhill Neocloud ETF"), "2026-08-21", -2.5)
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record["name"], "软件与云计算股票 ETF")
        self.assertIn("企业 IT 支出", record["description"])
        self.assertNotIn("去重后保留", record["description"])
        self.assertNotIn("Roundhill", record["description"])
        pharma = movers._record(row("FTXH", "First Trust Nasdaq Pharmaceuticals ETF"), "2026-08-21", 2.0)
        self.assertIsNotNone(pharma)
        assert pharma is not None
        self.assertEqual(pharma["name"], "医疗保健股票 ETF")
        inflation = movers._record(row("INFL", "Horizon Kinetics Inflation Beneficiaries ETF"), "2026-08-21", 2.0)
        self.assertIsNotNone(inflation)
        assert inflation is not None
        self.assertEqual(inflation["name"], "通胀受益股票 ETF")
        active = movers._record(row("AKRE", "Akre Focus ETF"), "2026-08-21", 2.0)
        self.assertEqual(active["name"], "主动精选股票 ETF")

    def test_current_misclassified_products_have_exact_distinct_details(self) -> None:
        products = {
            "GSG": ("iShares S&P GSCI Commodity-Indexed Trust", "S&P GSCI Total Return Index"),
            "COMT": ("iShares GSCI Commodity Dynamic Roll Strategy ETF", "S&P GSCI Dynamic Roll (USD) Total Return Index"),
            "FTXN": ("First Trust Nasdaq Oil & Gas ETF", "Nasdaq US Smart Oil & Gas Index"),
            "DRAM": ("Roundhill Memory ETF", "HBM、DRAM、NAND"),
            "PFIX": ("Simplify Interest Rate Hedge ETF", "20 年期美国国债看跌期权"),
            "CRAK": ("VanEck Oil Refiners ETF", "MVIS Global Oil Refiners Index"),
            "FCG": ("First Trust Natural Gas ETF", "而非天然气期货"),
            "BWET": ("Breakwave Tanker Shipping ETF", "而非航运股票"),
            "BLOK": ("Amplify Blockchain Technology ETF", "至少 80% 净资产"),
            "PDBC": ("Invesco Optimum Yield Diversified Commodity Strategy ETF", "DBIQ Optimum Yield Diversified Commodity Index Excess Return"),
            "XOP": ("SPDR S&P Oil & Gas Exploration & Production ETF", "S&P Oil & Gas Exploration & Production Select Industry Index"),
        }
        names: set[str] = set()
        descriptions: set[str] = set()
        for symbol, (fund_name, required_text) in products.items():
            record = movers._record(row(symbol, fund_name), "2026-08-25", -1.0)
            self.assertIsNotNone(record, symbol)
            assert record is not None
            self.assertIn(required_text, record["description"], symbol)
            self.assertNotIn("特色主题股票 ETF", record["name"], symbol)
            self.assertNotIn("聚焦基金名称所示", record["description"], symbol)
            names.add(str(record["name"]))
            descriptions.add(str(record["description"]))
        self.assertEqual(len(names), len(products))
        self.assertEqual(len(descriptions), len(products))

    def test_same_exposure_products_share_an_economic_driver_family(self) -> None:
        groups = [
            [
                row("BKCH", "Global X Blockchain ETF"),
                row("BLOK", "Amplify Blockchain Technology ETF"),
            ],
            [
                row("GSG", "iShares S&P GSCI Commodity-Indexed Trust"),
                row("COMT", "iShares GSCI Commodity Dynamic Roll Strategy ETF"),
                row("PDBC", "Invesco Optimum Yield Diversified Commodity Strategy ETF"),
            ],
            [
                row("FCG", "First Trust Natural Gas ETF"),
                row("XOP", "SPDR S&P Oil & Gas Exploration & Production ETF"),
                row("FTXN", "First Trust Nasdaq Oil & Gas ETF"),
            ],
        ]
        for products in groups:
            families = {
                movers.dedupe_family(product, movers.theme_key(product))
                for product in products
            }
            self.assertEqual(len(families), 1, [str(product["symbol"]) for product in products])

    def test_rank_keeps_only_one_product_per_shared_economic_driver(self) -> None:
        source_rows = [
            row("BKCH", "Global X Blockchain ETF", volume=100_000, change=5.48, assets=200_000_000),
            row("BLOK", "Amplify Blockchain Technology ETF", volume=800_000, change=3.46, assets=1_000_000_000),
            row("GSG", "iShares S&P GSCI Commodity-Indexed Trust", volume=200_000, change=-1.82, assets=900_000_000),
            row("COMT", "iShares GSCI Commodity Dynamic Roll Strategy ETF", volume=300_000, change=-1.71, assets=1_200_000_000),
            row("PDBC", "Invesco Optimum Yield Diversified Commodity Strategy ETF", volume=5_000_000, change=-1.62, assets=6_000_000_000),
            row("FCG", "First Trust Natural Gas ETF", volume=500_000, change=-1.90, assets=650_000_000),
            row("XOP", "SPDR S&P Oil & Gas Exploration & Production ETF", price=180, volume=3_000_000, change=-1.88, assets=3_800_000_000),
            row("FTXN", "First Trust Nasdaq Oil & Gas ETF", price=40, volume=700_000, change=-1.68, assets=160_000_000),
            row("CRAK", "VanEck Oil Refiners ETF", volume=200_000, change=-1.92, assets=230_000_000),
        ]
        records = []
        for source in source_rows:
            record = movers._record(source, "2026-08-25", float(source["regularMarketChangePercent"]))
            self.assertIsNotNone(record)
            assert record is not None
            records.append(record)

        gainers = movers._rank(records, True, 10)
        losers = movers._rank(records, False, 10)

        self.assertEqual([item["symbol"] for item in gainers], ["BLOK"])
        self.assertEqual([item["symbol"] for item in losers], ["CRAK", "XOP", "PDBC"])

    def test_fund_assets_break_a_liquidity_tie(self) -> None:
        small = row("BKCH", "Global X Blockchain ETF", volume=500_000, change=5.0, assets=200_000_000)
        large = row("BLOK", "Amplify Blockchain Technology ETF", volume=500_000, change=3.0, assets=1_000_000_000)
        records = []
        for source in (small, large):
            record = movers._record(source, "2026-08-25", float(source["regularMarketChangePercent"]))
            self.assertIsNotNone(record)
            assert record is not None
            records.append(record)
        ranked = movers._rank(records, True, 10)
        self.assertEqual([item["symbol"] for item in ranked], ["BLOK"])

    def test_unknown_theme_is_dropped_instead_of_using_placeholder_copy(self) -> None:
        unknown = movers._record(row("ZZZZ", "Example Distinctive Opportunities ETF"), "2026-08-25", 1.0)
        self.assertIsNone(unknown)


if __name__ == "__main__":
    unittest.main()
