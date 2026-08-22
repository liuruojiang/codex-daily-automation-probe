from __future__ import annotations

import json
import math
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo


MIN_PRICE = 5.0
MIN_AVG_DAILY_VOLUME = 50_000
MIN_AVG_DAILY_DOLLAR_VOLUME = 5_000_000.0
MAX_UNIVERSE_SCAN = 6_000
MAX_CHART_WORKERS = 16

QUERY_URL = "https://query1.finance.yahoo.com/v1/finance/screener"
QUERY_PARAMS = {
    "corsDomain": "finance.yahoo.com",
    "formatted": "false",
    "lang": "en-US",
    "region": "US",
}


LEVERAGED_OR_INVERSE_PATTERNS = (
    r"\b\d+(?:\.\d+)?x\b",
    r"\b-\s?1x\b",
    r"\bultra(?:pro|short)?\b",
    r"\binverse\b",
    r"\bshort\b(?![- ]?(?:term|duration|maturity|treasury|bond))",
    r"\bbull\b",
    r"\bbear\b",
    r"\bleverag(?:e|ed|es)\b",
    r"\bdaily\b.*\b(?:bull|bear|long|short)\b",
    r"\b(?:bull|bear)\b.*\b[23]x\b",
    r"\bmicrosectors\b",
)

OPTION_INCOME_PATTERNS = (
    r"\byieldmax\b",
    r"\bweeklypay\b",
    r"\bincomemax\b",
    r"\boption income\b",
    r"\bincome strategy\b",
    r"\bgrowth\s*(?:&|and)\s*income\b",
    r"\byield\s*boost\b",
    r"\bpremium income\b",
    r"\benhanced income\b",
    r"\bcovered call\b",
    r"\bbuy[- ]?write\b",
    r"\bput[- ]?write\b",
    r"\bcall writing\b",
    r"\boption strategy\b",
    r"\btarget income\b",
    r"\bdefined outcome\b",
    r"\bbuffer(?:ed)?\b",
    r"\bkurv\b",
)

SINGLE_CRYPTO_PATTERNS = (
    r"\bbitcoin\b",
    r"\bether(?:eum)?\b",
    r"\bsolana\b",
    r"\bchainlink\b",
    r"\bzcash\b",
    r"\bbittensor\b",
    r"\bxrp\b",
    r"\bdoge(?:coin)?\b",
    r"\bhyperliquid\b",
    r"\bsui\b",
    r"\bcanton network\b",
    r"\bstaking\b",
)

THEME_PATTERNS = (
    ("volatility_futures", r"\b(?:vix|volatility)\b"),
    ("managed_futures", r"\b(?:managed futures?|trend following|trend strategy)\b"),
    ("broad_commodities", r"\b(?:diversified commodity|broad commodity|commodity index|commodity strategy|optimum yield)\b"),
    ("crude_oil_futures", r"\b(?:crude oil|brent oil|oil fund|oil futures?)\b"),
    ("natural_gas_futures", r"\b(?:natural gas|gasoline)\b"),
    ("agriculture_futures", r"\b(?:agriculture|corn|wheat|soybean|sugar|coffee)\b"),
    ("gold_miners", r"\bgold\b.*\b(?:miners?|mining)\b"),
    ("silver_miners", r"\bsilver\b.*\b(?:miners?|mining)\b"),
    ("copper_miners", r"\bcopper\b.*\b(?:miners?|mining)\b"),
    ("gold_futures", r"\bgold\b"),
    ("silver_futures", r"\bsilver\b"),
    ("copper_futures", r"\bcopper\b"),
    ("uranium_nuclear", r"\b(?:uranium|nuclear)\b"),
    ("shipping", r"\b(?:shipping|tanker|freight)\b"),
    ("semiconductors", r"\b(?:semiconductors?|chips?)\b"),
    ("photonics", r"\b(?:photonics?|optical technology|laser technology)\b"),
    ("cybersecurity", r"\b(?:cybersecurity|cyber security)\b"),
    ("software_cloud", r"\b(?:software|cloud computing|saas)\b"),
    ("ai_robotics", r"\b(?:ai|artificial intelligence|robotics|humanoid|automation)\b"),
    ("biotech_genomics", r"\b(?:biotech|biotechnology|genomic)\b"),
    ("healthcare", r"\b(?:health care|healthcare|medical|pharma)\b"),
    ("financials", r"\b(?:financial|bank|insurance|broker)\b"),
    ("real_estate", r"\b(?:real estate|reit)\b"),
    ("consumer_discretionary", r"\b(?:consumer discretionary|retail)\b"),
    ("consumer_staples", r"\b(?:consumer staples|staples)\b"),
    ("defense_aerospace", r"\b(?:aerospace|defense|drone|warfare)\b"),
    ("industrials", r"\bindustrials?\b"),
    ("materials", r"\b(?:materials|metals\s*(?:&|and)\s*mining)\b"),
    ("utilities", r"\butilit(?:y|ies)\b"),
    ("energy_infrastructure", r"\b(?:energy infrastructure|mlp.*energy|midstream|pipeline)\b"),
    ("energy_equity", r"\b(?:energy equity|energy sector|exploration)\b"),
    ("clean_energy", r"\b(?:clean energy|solar|wind|hydrogen|battery)\b"),
    ("communications", r"\bcommunication services?\b"),
    ("technology", r"\btechnology\b"),
    ("china_equity", r"\b(?:china|chinese|csi 300)\b"),
    ("japan_equity", r"\b(?:japan|nikkei)\b"),
    ("india_equity", r"\b(?:india|nifty)\b"),
    ("korea_equity", r"\b(?:south korea|korea)\b"),
    ("taiwan_equity", r"\btaiwan\b"),
    ("brazil_equity", r"\bbrazil\b"),
    ("germany_equity", r"\bgermany\b"),
    ("uk_equity", r"\b(?:united kingdom|uk equity)\b"),
    ("emerging_markets", r"\bemerging markets?\b"),
    ("developed_ex_us", r"\b(?:developed markets?|eafe)\b"),
    ("small_cap_equity", r"\b(?:small cap|small-cap|russell 2000)\b"),
    ("mid_cap_equity", r"\b(?:mid cap|mid-cap)\b"),
    ("large_cap_growth", r"\b(?:large cap growth|large-cap growth|nasdaq[- ]?100)\b"),
    ("large_cap_value", r"\b(?:large cap value|large-cap value)\b"),
    ("large_cap_equity", r"\b(?:s&p\s*500|sp 500|large cap|large-cap)\b"),
    ("momentum_factor", r"\bmomentum\b"),
    ("quality_factor", r"\bquality\b"),
    ("value_factor", r"\bvalue factor\b"),
    ("low_vol_factor", r"\b(?:low|min(?:imum)?) volatility\b"),
    ("dividend_equity", r"\bdividend\b"),
    ("treasury_long", r"\b(?:20\+ year|long[- ]term treasury|long treasury)\b"),
    ("treasury_intermediate", r"\b(?:7[- ]10 year|intermediate treasury)\b"),
    ("treasury_short", r"\b(?:short[- ]term treasury|1[- ]3 year|treasury bill|t[- ]bill)\b"),
    ("tips", r"\b(?:tips|inflation[- ]protected)\b"),
    ("investment_grade_credit", r"\b(?:investment grade|corporate bond)\b"),
    ("high_yield_credit", r"\b(?:high yield|junk bond)\b"),
    ("municipal_bonds", r"\b(?:municipal|muni)\b"),
    ("emerging_market_bonds", r"\bemerging market.*bond\b"),
    ("preferreds", r"\bpreferred\b"),
    ("us_dollar_futures", r"\b(?:us dollar|u\.s\. dollar|dollar index)\b"),
    ("currency_futures", r"\b(?:currency|euro|yen|swiss franc|australian dollar)\b"),
    ("crypto_index", r"\b(?:crypto|digital asset|blockchain|digital transformation)\b"),
)

ISSUER_WORDS = {
    "advisorshares", "amplify", "ark", "bitwise", "canary", "capital", "direxion", "dimensional",
    "etf", "first", "fidelity", "franklin", "fund", "global", "goldman", "graniteshares", "index",
    "invesco", "ishares", "jpmorgan", "kfa", "pacer", "proshares", "rex", "roundhill", "schwab",
    "shares", "spdr", "state", "street", "strategy", "tuttle", "vanguard", "vaneck", "wisdomtree",
}

DESCRIPTIONS = {
    "volatility_futures": "波动率期货 ETF，主要反映 VIX 期货曲线及展期损益。",
    "managed_futures": "管理期货/趋势策略 ETF，通过期货覆盖多个资产类别。",
    "broad_commodities": "多商品期货 ETF，覆盖能源、金属和农产品等期货敞口。",
    "crude_oil_futures": "原油期货 ETF，收益会受到期货曲线和展期损益影响。",
    "natural_gas_futures": "天然气期货 ETF，波动高且受期货展期影响明显。",
    "agriculture_futures": "农产品期货 ETF，反映相关农产品期货价格及展期损益。",
    "gold_futures": "黄金期货策略 ETF，提供黄金价格及期货曲线敞口。",
    "silver_futures": "白银期货策略 ETF，提供白银价格及期货曲线敞口。",
    "copper_futures": "铜期货 ETF，主要反映工业金属周期与期货曲线。",
    "copper_miners": "铜矿股 ETF，主要反映铜价、矿企盈利和权益市场风险。",
    "crypto_index": "多资产加密或区块链 ETF；已排除单一加密资产产品。",
}


@dataclass(frozen=True)
class RankingResult:
    universe_count: int
    eligible_count: int
    excluded_counts: dict[str, int]
    gainers: list[dict[str, Any]]
    losers: list[dict[str, Any]]


def _name(row: dict[str, Any]) -> str:
    return str(row.get("longName") or row.get("shortName") or row.get("symbol") or "")


def _text(row: dict[str, Any]) -> str:
    return f"{row.get('symbol') or ''} {_name(row)}".lower()


def _matches(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, flags=re.I) for pattern in patterns)


def exclusion_reason(row: dict[str, Any]) -> str | None:
    text = _text(row)
    if _matches(text, LEVERAGED_OR_INVERSE_PATTERNS):
        return "leveraged_or_inverse"
    if _matches(text, OPTION_INCOME_PATTERNS):
        return "option_income_or_defined_outcome"
    if re.search(r"\b(?:single[- ]stock|single stock|daily target)\b", text):
        return "single_stock"
    if re.search(r"\b(?:etn|exchange traded note|exchange-traded note)\b", text):
        return "etn"
    if _matches(text, SINGLE_CRYPTO_PATTERNS):
        return "single_crypto"
    if re.search(r"\b(?:physical|bullion|spot)\b", text):
        return "physical_or_spot_trust"
    if re.search(r"\b(?:gold|silver)\b.*\b(?:trust|shares)\b", text):
        return "physical_or_spot_trust"
    return None


def average_daily_volume(row: dict[str, Any]) -> int:
    value = row.get("averageDailyVolume3Month") or row.get("averageDailyVolume10Day") or row.get("regularMarketVolume") or 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def average_daily_dollar_volume(row: dict[str, Any]) -> float:
    try:
        price = float(row.get("regularMarketPrice") or 0)
    except (TypeError, ValueError):
        price = 0.0
    return price * average_daily_volume(row)


def is_liquid(row: dict[str, Any]) -> bool:
    return (
        average_daily_volume(row) >= MIN_AVG_DAILY_VOLUME
        and average_daily_dollar_volume(row) >= MIN_AVG_DAILY_DOLLAR_VOLUME
    )


def theme_key(row: dict[str, Any]) -> str:
    text = _text(row)
    for key, pattern in THEME_PATTERNS:
        if re.search(pattern, text, flags=re.I):
            return key
    tokens = [token for token in re.findall(r"[a-z0-9]+", _name(row).lower()) if token not in ISSUER_WORDS]
    return "name_" + "_".join(tokens[:4]) if tokens else "symbol_" + str(row.get("symbol") or "").lower()


def description(row: dict[str, Any], key: str) -> str:
    if key in DESCRIPTIONS:
        return DESCRIPTIONS[key]
    return f"{_name(row)}，与同类产品按底层资产或策略主题去重后保留。"


def _screener_body(offset: int, size: int, min_price: float) -> dict[str, Any]:
    return {
        "offset": offset,
        "size": size,
        "sortType": "DESC",
        "sortField": "percentchange",
        "quoteType": "ETF",
        "query": {
            "operator": "and",
            "operands": [
                {"operator": "gt", "operands": ["intradayprice", min_price]},
                {"operator": "EQ", "operands": ["region", "us"]},
            ],
        },
    }


def _fetch_page(offset: int, size: int, min_price: float) -> list[dict[str, Any]]:
    try:
        from yfinance.data import YfData
    except ImportError as exc:
        raise RuntimeError("yfinance is required for broad ETF rankings") from exc
    response = YfData().post(
        QUERY_URL,
        body=_screener_body(offset, size, min_price),
        params=QUERY_PARAMS,
    )
    response.raise_for_status()
    result = response.json().get("finance", {}).get("result") or []
    return (result[0].get("quotes") or []) if result else []


def fetch_universe(max_scan: int = MAX_UNIVERSE_SCAN, min_price: float = MIN_PRICE) -> list[dict[str, Any]]:
    rows_by_symbol: dict[str, dict[str, Any]] = {}
    page_size = 250
    for offset in range(0, max_scan, page_size):
        page = _fetch_page(offset, min(page_size, max_scan - offset), min_price)
        if not page:
            break
        for row in page:
            symbol = str(row.get("symbol") or "")
            if symbol:
                rows_by_symbol[symbol] = row
    return list(rows_by_symbol.values())


def eligible_universe(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    eligible: list[dict[str, Any]] = []
    excluded: dict[str, int] = {}
    for row in rows:
        reason = exclusion_reason(row)
        if reason:
            excluded[reason] = excluded.get(reason, 0) + 1
            continue
        if not is_liquid(row):
            excluded["insufficient_liquidity"] = excluded.get("insufficient_liquidity", 0) + 1
            continue
        eligible.append(row)
    return eligible, excluded


def _rank(records: list[dict[str, Any]], reverse: bool, limit: int) -> list[dict[str, Any]]:
    picked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in sorted(records, key=lambda item: float(item["change"]), reverse=reverse):
        if reverse and float(record["change"]) <= 0:
            continue
        if not reverse and float(record["change"]) >= 0:
            continue
        key = str(record["category"])
        if key in seen:
            continue
        seen.add(key)
        picked.append(record)
        if len(picked) >= limit:
            break
    return picked


def _record(row: dict[str, Any], date_s: str, change: float) -> dict[str, Any]:
    key = theme_key(row)
    return {
        "symbol": str(row.get("symbol") or ""),
        "name": _name(row),
        "description": description(row, key),
        "category": key,
        "date": date_s,
        "change": change,
        "average_daily_volume": average_daily_volume(row),
        "average_daily_dollar_volume": average_daily_dollar_volume(row),
    }


def daily_rankings(rows: list[dict[str, Any]], limit: int = 10) -> RankingResult:
    eligible, excluded = eligible_universe(rows)
    ny = ZoneInfo("America/New_York")
    records: list[dict[str, Any]] = []
    for row in eligible:
        value = row.get("regularMarketChangePercent")
        if value is None:
            continue
        timestamp = int(row.get("regularMarketTime") or 0)
        date_s = datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone(ny).date().isoformat() if timestamp else ""
        records.append(_record(row, date_s, float(value)))
    return RankingResult(len(rows), len(eligible), excluded, _rank(records, True, limit), _rank(records, False, limit))


def _chart_rows(symbol: str) -> list[tuple[str, float]]:
    encoded = urllib.parse.quote(symbol)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range=3mo&interval=1d&events=div%2Csplits"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = json.load(resp)
            result = payload.get("chart", {}).get("result") or []
            if not result:
                return []
            data = result[0]
            timestamps = data.get("timestamp") or []
            closes = ((data.get("indicators", {}).get("quote") or [{}])[0]).get("close") or []
            values: list[tuple[str, float]] = []
            for ts, close in zip(timestamps, closes):
                if close is None or not math.isfinite(float(close)):
                    continue
                values.append((datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat(), float(close)))
            return values
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.25)
    if last_error:
        raise last_error
    return []


def period_rankings(rows: list[dict[str, Any]], limit: int = 10) -> dict[str, Any]:
    eligible, excluded = eligible_universe(rows)
    chart_by_symbol: dict[str, list[tuple[str, float]]] = {}
    chart_errors = 0
    with ThreadPoolExecutor(max_workers=MAX_CHART_WORKERS) as pool:
        futures = {pool.submit(_chart_rows, str(row.get("symbol") or "")): str(row.get("symbol") or "") for row in eligible}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                chart_by_symbol[symbol] = future.result()
            except Exception:
                chart_errors += 1

    result: dict[str, Any] = {
        "universe_count": len(rows),
        "eligible_count": len(eligible),
        "excluded_counts": excluded,
        "chart_errors": chart_errors,
    }
    for label, bars_back in (("one_week", 5), ("one_month", 21)):
        records: list[dict[str, Any]] = []
        for row in eligible:
            chart = chart_by_symbol.get(str(row.get("symbol") or ""), [])
            if len(chart) <= bars_back:
                continue
            start_date, start_close = chart[-1 - bars_back]
            end_date, end_close = chart[-1]
            if start_close <= 0:
                continue
            records.append(_record(row, end_date, (end_close / start_close - 1.0) * 100.0))
        result[label] = {
            "gainers": _rank(records, True, limit),
            "losers": _rank(records, False, limit),
        }
    return result
