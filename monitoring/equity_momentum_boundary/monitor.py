"""Weekly research monitor for equity time-series momentum boundaries.

The monitor is deliberately a report-only workflow.  It does not emit orders,
portfolio weights, or production signals.  The core calculation follows the
paper's observable structure: 12-month moving averages, rolling 10/20-year
min/max normalization, and a two-component boundary score.  The term spread is
an explicitly labelled public proxy (FRED GS10 minus TB3MS), because the paper's
Ibbotson long-bond series is not distributed as a live feed here.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import pandas as pd


MONITOR_NAME = "Weekly Equity Momentum Boundary Monitor"
PAPER_URL = "https://onlinelibrary.wiley.com/doi/full/10.1111/fima.70055"
SHILLER_PAGE_URL = "https://shillerdata.com/"
FRED_GRAPH_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
FRED_SERIES_URL = "https://fred.stlouisfed.org/series/{series_id}"
H15_TREASURY_CSV_URL = (
    "https://www.federalreserve.gov/datadownload/Output.aspx?"
    "rel=H15&series=bf17364827e38702b42a58cf8eaa3f78&lastobs=&from=&to="
    "&filetype=csv&label=include&layout=seriescolumn&type=package"
)
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=2y&interval=1d"
USER_AGENT = "equity-momentum-boundary-monitor/1.0"


@dataclass(frozen=True)
class SourceResult:
    name: str
    url: str
    as_of: str | None
    status: str
    detail: str | None = None


def fetch_bytes(url: str, timeout: int = 20) -> bytes:
    # The Windows runner used for local verification can stall on urllib while
    # curl negotiates the same public endpoints successfully.  GitHub-hosted
    # Ubuntu runners also provide curl, so use the same bounded path in both
    # environments and keep urllib as a narrow fallback.
    curl = shutil.which("curl") or shutil.which("curl.exe")
    if curl:
        completed = subprocess.run(
            [curl, "--fail", "--location", "--silent", "--show-error", "--max-time", str(timeout), "--user-agent", USER_AGENT, url],
            check=True,
            capture_output=True,
        )
        return completed.stdout
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - URL is fixed/configured
        return response.read()


def discover_shiller_url() -> str:
    """Read the current official download link instead of hard-coding a CDN version."""

    html = fetch_bytes(SHILLER_PAGE_URL).decode("utf-8", errors="replace")
    matches = re.findall(r'href=["\']([^"\']*ie_data\.xls[^"\']*)', html, flags=re.I)
    if not matches:
        raise RuntimeError("Shiller official page did not expose ie_data.xls")
    return urljoin(SHILLER_PAGE_URL, matches[0])


def _parse_shiller_periods(values: pd.Series) -> pd.Series:
    raw = values.astype(str).str.extract(r"(?P<year>\d{4})\.(?P<month>\d{1,2})")
    year = pd.to_numeric(raw["year"], errors="coerce")
    month = pd.to_numeric(raw["month"], errors="coerce")
    out = pd.Series(pd.NaT, index=values.index, dtype="period[M]")
    valid = year.notna() & month.notna() & month.between(1, 12)
    if valid.any():
        out.loc[valid] = pd.PeriodIndex(
            [f"{int(y):04d}-{int(m):02d}" for y, m in zip(year[valid], month[valid])],
            freq="M",
        )
    return out


def load_shiller_data() -> tuple[pd.DataFrame, SourceResult]:
    url = discover_shiller_url()
    payload = fetch_bytes(url)
    frame = pd.read_excel(io.BytesIO(payload), sheet_name="Data", header=7)
    required = {"Date", "P", "D", "CAPE"}
    missing = required.difference(frame.columns)
    if missing:
        raise RuntimeError(f"Shiller Data sheet missing columns: {sorted(missing)}")

    out = pd.DataFrame(
        {
            "period": _parse_shiller_periods(frame["Date"]),
            "price": pd.to_numeric(frame["P"], errors="coerce"),
            "dividend": pd.to_numeric(frame["D"], errors="coerce"),
            "cape": pd.to_numeric(frame["CAPE"], errors="coerce"),
        }
    )
    out = out.dropna(subset=["period"]).sort_values("period").drop_duplicates("period", keep="last")
    out["dividend_yield"] = out["dividend"] / out["price"]
    out.loc[~out["dividend_yield"].between(0, 1), "dividend_yield"] = float("nan")
    out = out.set_index("period")
    latest = out.index.max().strftime("%Y-%m")
    source = SourceResult("Shiller CAPE and dividends", SHILLER_PAGE_URL, latest, "ok", url)
    return out, source


def load_fred_series(series_id: str) -> tuple[pd.Series, SourceResult]:
    series_map = load_fred_series_bulk([series_id])
    return series_map[series_id]


def load_fred_series_bulk(series_ids: list[str]) -> dict[str, tuple[pd.Series, SourceResult]]:
    """Fetch several public FRED CSV series in one request.

    FRED's graph endpoint accepts comma-separated IDs.  A single bounded
    request makes the weekly job faster and prevents one unavailable series
    from leaving a long chain of sequential network timeouts.
    """

    if not series_ids:
        return {}
    joined_ids = ",".join(series_ids)
    url = FRED_GRAPH_URL.format(series_id=joined_ids)
    payload = fetch_bytes(url)
    frame = pd.read_csv(io.BytesIO(payload))
    if "observation_date" not in frame.columns:
        raise RuntimeError(f"FRED response has unexpected columns: {frame.columns.tolist()}")
    dates = pd.to_datetime(frame["observation_date"], errors="coerce")
    result: dict[str, tuple[pd.Series, SourceResult]] = {}
    for series_id in series_ids:
        if series_id not in frame.columns:
            raise RuntimeError(f"FRED response omitted {series_id}; columns: {frame.columns.tolist()}")
        values = pd.to_numeric(frame[series_id], errors="coerce")
        series = pd.Series(values.to_numpy(), index=dates).dropna()
        series = series[~series.index.isna()].sort_index()
        if series.empty:
            raise RuntimeError(f"FRED {series_id} returned no numeric observations")
        as_of = series.index.max().strftime("%Y-%m-%d")
        result[series_id] = (
            series,
            SourceResult(series_id, FRED_SERIES_URL.format(series_id=series_id), as_of, "ok", url),
        )
    return result


def load_h15_treasury_rates() -> tuple[pd.DataFrame, list[SourceResult]]:
    """Load official Federal Reserve H.15 Treasury constant-maturity rates.

    The H.15 download is a bounded fallback for the FRED graph endpoint.  It
    contains the 3-month, 2-year, and 10-year Treasury series needed by the
    core term-spread proxy and its curve context.
    """

    payload = fetch_bytes(H15_TREASURY_CSV_URL)
    frame = pd.read_csv(io.BytesIO(payload), skiprows=5, dtype=str)
    columns = {
        "date": "Time Period",
        "three_month": "RIFLGFCM03_N.B",
        "two_year": "RIFLGFCY02_N.B",
        "ten_year": "RIFLGFCY10_N.B",
    }
    missing = set(columns.values()).difference(frame.columns)
    if missing:
        raise RuntimeError(f"Federal Reserve H.15 response omitted {sorted(missing)}")
    out = pd.DataFrame(index=pd.to_datetime(frame[columns["date"]], errors="coerce"))
    for key, column in columns.items():
        if key != "date":
            out[key] = pd.to_numeric(frame[column], errors="coerce").to_numpy()
    out = out[~out.index.isna()].sort_index()
    if out[["three_month", "ten_year"]].dropna(how="all").empty:
        raise RuntimeError("Federal Reserve H.15 returned no Treasury observations")
    sources: list[SourceResult] = []
    for key, label in (
        ("three_month", "H.15 3M Treasury constant maturity"),
        ("two_year", "H.15 2Y Treasury constant maturity"),
        ("ten_year", "H.15 10Y Treasury constant maturity"),
    ):
        series = out[key].dropna()
        sources.append(
            SourceResult(
                label,
                H15_TREASURY_CSV_URL,
                series.index.max().strftime("%Y-%m-%d") if not series.empty else None,
                "ok",
                "official Federal Reserve H.15 fallback for the unavailable FRED graph endpoint",
            )
        )
    return out, sources


def load_yahoo_chart_series(symbol: str, label: str) -> tuple[pd.Series, SourceResult]:
    """Load a daily index series from Yahoo's public chart endpoint as a labelled proxy."""

    url = YAHOO_CHART_URL.format(symbol=symbol)
    payload = json.loads(fetch_bytes(url).decode("utf-8"))
    result = (((payload.get("chart") or {}).get("result") or [None])[0])
    if not isinstance(result, dict) or not result.get("timestamp"):
        raise RuntimeError(f"Yahoo chart returned no observations for {symbol}")
    timestamps = pd.to_datetime(result["timestamp"], unit="s", utc=True).tz_convert(None)
    quote = (((result.get("indicators") or {}).get("quote") or [None])[0])
    closes = quote.get("close") if isinstance(quote, dict) else None
    if not isinstance(closes, list):
        raise RuntimeError(f"Yahoo chart omitted close values for {symbol}")
    series = pd.Series(pd.to_numeric(closes, errors="coerce"), index=timestamps).dropna().sort_index()
    if series.empty:
        raise RuntimeError(f"Yahoo chart returned no numeric observations for {symbol}")
    return series, SourceResult(
        label,
        url,
        series.index.max().strftime("%Y-%m-%d"),
        "ok",
        "Yahoo Finance chart API fallback; proxy for the unavailable FRED series",
    )


def build_term_spread_proxy() -> tuple[pd.DataFrame, list[SourceResult], list[str]]:
    """Monthly long-minus-short Treasury proxy matching the paper's structure."""

    warnings: list[str] = []
    try:
        fred = load_fred_series_bulk(["GS10", "TB3MS"])
        long_rate, long_source = fred["GS10"]
        short_rate, short_source = fred["TB3MS"]
        two_year_rate = None
        sources = [long_source, short_source]
    except Exception as exc:  # noqa: BLE001 - use an official fallback, with disclosure
        h15, h15_sources = load_h15_treasury_rates()
        long_rate = h15["ten_year"].dropna()
        short_rate = h15["three_month"].dropna()
        two_year_rate = h15["two_year"].dropna()
        sources = h15_sources
        warnings.append(f"FRED term-spread data unavailable; used official Federal Reserve H.15 fallback: {exc}")
    long_month = long_rate.groupby(long_rate.index.to_period("M")).last()
    short_month = short_rate.groupby(short_rate.index.to_period("M")).last()
    frame = pd.concat({"long_rate": long_month, "short_rate": short_month}, axis=1).dropna()
    frame["term_spread"] = frame["long_rate"] - frame["short_rate"]
    if two_year_rate is not None:
        two_year_month = two_year_rate.groupby(two_year_rate.index.to_period("M")).last()
        frame["two_year_rate"] = two_year_month.reindex(frame.index)
        frame["term_spread_10y2y"] = frame["long_rate"] - frame["two_year_rate"]
    return frame, sources, warnings


def scale_to_signed(value: float, low: float, high: float) -> float:
    """Map a value in [low, high] to [-1, +1]."""

    if not all(math.isfinite(float(x)) for x in (value, low, high)) or high <= low:
        return float("nan")
    bounded = min(max(float(value), low), high)
    return 2.0 * (bounded - low) / (high - low) - 1.0


def rolling_percentile(values: pd.Series, window: int) -> pd.Series:
    result = pd.Series(float("nan"), index=values.index, dtype=float)
    for i in range(len(values)):
        if i + 1 < window or pd.isna(values.iloc[i]):
            continue
        history = values.iloc[i - window + 1 : i + 1].dropna()
        if len(history) < window:
            continue
        result.iloc[i] = float((history <= values.iloc[i]).mean())
    return result


def boundary_component(values: pd.Series, window: int) -> pd.DataFrame:
    """Return paper-like 12-month average, scaling, score, and tail flag."""

    values = pd.to_numeric(values, errors="coerce").sort_index()
    moving = values.rolling(12, min_periods=12).mean()
    low = moving.rolling(window, min_periods=window).min()
    high = moving.rolling(window, min_periods=window).max()
    scaled = pd.Series(
        [scale_to_signed(v, lo, hi) for v, lo, hi in zip(moving, low, high)],
        index=values.index,
        dtype=float,
    )
    percentile = rolling_percentile(moving, window)
    return pd.DataFrame(
        {
            "raw": values,
            "ma12": moving,
            "rolling_min": low,
            "rolling_max": high,
            "scaled": scaled,
            "score": scaled.pow(2),
            "percentile": percentile,
            "extreme": percentile.le(0.10) | percentile.ge(0.90),
        },
        index=values.index,
    )


def _safe_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _period_string(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    return str(value)


def _period_month_distance(later: pd.Period, earlier: pd.Period) -> int:
    return (later.year - earlier.year) * 12 + (later.month - earlier.month)


def _boundary_state(extreme_count: int) -> str:
    return {0: "mid-regime", 1: "elevated", 2: "boundary"}.get(extreme_count, "unknown")


def build_boundary_variant(
    shiller: pd.DataFrame,
    term: pd.DataFrame,
    value_column: str,
    value_label: str,
    window: int,
) -> dict[str, Any]:
    combined = pd.concat(
        [shiller[value_column].rename("valuation"), term["term_spread"].rename("term_spread")],
        axis=1,
        join="inner",
    ).sort_index()
    combined = combined.dropna(how="all")
    if combined.empty:
        return {"status": "blocked", "detail": "no common monthly observations"}

    valuation = boundary_component(combined["valuation"], window).add_prefix("valuation_")
    spread = boundary_component(combined["term_spread"], window).add_prefix("term_")
    result = pd.concat([combined, valuation, spread], axis=1)
    result["boundary_score"] = result["valuation_score"] + result["term_score"]
    result["extreme_count"] = result["valuation_extreme"].astype("Int64") + result["term_extreme"].astype("Int64")
    valid = result.dropna(subset=["boundary_score", "extreme_count"])
    if valid.empty:
        return {"status": "warming", "detail": f"fewer than {window} years of valid history"}
    row = valid.iloc[-1]
    return {
        "status": "ok",
        "window_years": window // 12,
        "value_label": value_label,
        "as_of": _period_string(valid.index[-1]),
        "valuation_raw": _safe_float(row["valuation"]),
        "valuation_ma12": _safe_float(row["valuation_ma12"]),
        "valuation_percentile": _safe_float(row["valuation_percentile"]),
        "valuation_scaled": _safe_float(row["valuation_scaled"]),
        "valuation_extreme": bool(row["valuation_extreme"]),
        "term_spread_raw": _safe_float(row["term_spread"]),
        "term_spread_ma12": _safe_float(row["term_ma12"]),
        "term_spread_percentile": _safe_float(row["term_percentile"]),
        "term_spread_scaled": _safe_float(row["term_scaled"]),
        "term_spread_extreme": bool(row["term_extreme"]),
        "boundary_score_0_to_2": _safe_float(row["boundary_score"]),
        "extreme_components": int(row["extreme_count"]),
        "state": _boundary_state(int(row["extreme_count"])),
        "method": "12M moving average; rolling min/max; squared signed components; 10th/90th percentile tail flag",
    }


def _weekly_change(series: pd.Series) -> float | None:
    if series.empty:
        return None
    latest_date = series.index[-1]
    prior = series.loc[series.index <= latest_date - pd.Timedelta(days=7)]
    if prior.empty:
        return None
    return _safe_float(series.iloc[-1] - prior.iloc[-1])


def _percentile_of_tail(series: pd.Series, observations: int = 252) -> float | None:
    series = series.dropna()
    if len(series) < observations:
        return None
    tail = series.iloc[-observations:]
    return _safe_float((tail <= tail.iloc[-1]).mean())


def _build_sp500_context(series: pd.Series, source: SourceResult) -> dict[str, Any]:
    latest = series.iloc[-1]
    result: dict[str, Any] = {
        "as_of": source.as_of,
        "price": _safe_float(latest),
        "weekly_change": _weekly_change(series),
        "momentum_1m": _safe_float(latest / series.iloc[-22] - 1) if len(series) >= 22 else None,
        "momentum_3m": _safe_float(latest / series.iloc[-64] - 1) if len(series) >= 64 else None,
        "momentum_6m": _safe_float(latest / series.iloc[-127] - 1) if len(series) >= 127 else None,
        "momentum_12m": _safe_float(latest / series.iloc[-253] - 1) if len(series) >= 253 else None,
    }
    log_return = (series / series.shift(1)).apply(lambda x: math.log(x) if x > 0 else float("nan"))
    result["realized_vol_20d_annualized"] = _safe_float(log_return.tail(20).std(ddof=1) * math.sqrt(252)) if len(log_return) >= 20 else None
    result["recent_reversal_flag"] = bool(
        result["momentum_12m"] is not None
        and result["momentum_1m"] is not None
        and result["momentum_12m"] * result["momentum_1m"] < 0
    )
    return result


def _add_series_context(context: dict[str, Any], label: str, series: pd.Series, source: SourceResult) -> None:
    context[label] = {
        "as_of": source.as_of,
        "value": _safe_float(series.iloc[-1]),
        "weekly_change": _weekly_change(series),
        "one_year_percentile": _percentile_of_tail(series),
        "source_url": source.url,
    }


def build_supplemental_context() -> tuple[dict[str, Any], list[SourceResult], list[str]]:
    context: dict[str, Any] = {}
    sources: list[SourceResult] = []
    warnings: list[str] = []

    try:
        fred = load_fred_series_bulk(["SP500", "VIXCLS", "BAA10Y", "T10Y3M", "T10Y2Y"])
        sp500, sp500_source = fred["SP500"]
        sources.append(sp500_source)
        context["sp500_price_momentum"] = _build_sp500_context(sp500, sp500_source)
        for series_id, label in (("VIXCLS", "vix"), ("BAA10Y", "baa_minus_10y"), ("T10Y3M", "term_spread_10y3m"), ("T10Y2Y", "term_spread_10y2y")):
            series, source = fred[series_id]
            sources.append(source)
            _add_series_context(context, label, series, source)
        return context, sources, warnings
    except Exception as exc:  # noqa: BLE001 - use labelled public fallbacks
        warnings.append(f"FRED supplemental context unavailable; trying public fallbacks: {exc}")

    try:
        sp500, source = load_yahoo_chart_series("%5EGSPC", "Yahoo Finance S&P 500")
        sources.append(source)
        context["sp500_price_momentum"] = _build_sp500_context(sp500, source)
    except Exception as exc:  # noqa: BLE001 - disclose unavailable supplemental data
        warnings.append(f"S&P 500 supplemental fallback unavailable: {exc}")

    try:
        vix, source = load_yahoo_chart_series("%5EVIX", "Yahoo Finance VIX")
        sources.append(source)
        _add_series_context(context, "vix", vix, source)
    except Exception as exc:  # noqa: BLE001 - disclose unavailable supplemental data
        warnings.append(f"VIX supplemental fallback unavailable: {exc}")

    try:
        h15, h15_sources = load_h15_treasury_rates()
        sources.extend(h15_sources)
        ten_year = h15["ten_year"].dropna()
        three_month = h15["three_month"].dropna()
        two_year = h15["two_year"].dropna()
        curve_10y3m = (ten_year - three_month).dropna()
        curve_10y2y = (ten_year - two_year).dropna()
        curve_source = SourceResult(
            "H.15 Treasury curve fallback",
            H15_TREASURY_CSV_URL,
            curve_10y3m.index.max().strftime("%Y-%m-%d") if not curve_10y3m.empty else None,
            "ok",
            "official Federal Reserve H.15 10Y-minus-3M/2Y curve fallback",
        )
        _add_series_context(context, "term_spread_10y3m", curve_10y3m, curve_source)
        _add_series_context(context, "term_spread_10y2y", curve_10y2y, curve_source)
    except Exception as exc:  # noqa: BLE001 - disclose unavailable supplemental data
        warnings.append(f"Treasury curve supplemental fallback unavailable: {exc}")

    warnings.append("BAA10Y supplemental context has no configured public fallback and is omitted when FRED is unavailable")
    return context, sources, warnings


def _source_dict(source: SourceResult) -> dict[str, Any]:
    return {"name": source.name, "url": source.url, "as_of": source.as_of, "status": source.status, "detail": source.detail}


def build_report() -> dict[str, Any]:
    warnings: list[str] = []
    sources: list[SourceResult] = []
    core: dict[str, Any] = {}
    try:
        shiller, shiller_source = load_shiller_data()
        sources.append(shiller_source)
        cape_latest = shiller["cape"].dropna().index.max()
        dividend_latest = shiller["dividend_yield"].dropna().index.max()
        if pd.notna(cape_latest) and pd.notna(dividend_latest) and _period_month_distance(cape_latest, dividend_latest) > 1:
            warnings.append(
                f"Shiller dividend yield is stale relative to CAPE: latest complete {dividend_latest}, CAPE {cape_latest}"
            )
        term, term_sources, term_warnings = build_term_spread_proxy()
        sources.extend(term_sources)
        warnings.extend(term_warnings)
        for value_column, label in (("cape", "Shiller CAPE"), ("dividend_yield", "market dividend yield")):
            key = "cape" if value_column == "cape" else "dividend_yield"
            for years in (10, 20):
                core[f"{key}_boundary_{years}y"] = build_boundary_variant(
                    shiller,
                    term,
                    value_column,
                    label,
                    years * 12,
                )
        core["term_spread_proxy"] = {
            "definition": "FRED GS10 minus TB3MS; monthly public proxy for the paper's long-bond minus short-Treasury term spread",
            "latest_as_of": str(term.index.max()),
            "latest_value": _safe_float(term["term_spread"].iloc[-1]),
            "cape_latest_as_of": _period_string(cape_latest),
            "dividend_yield_latest_as_of": _period_string(dividend_latest),
            "source_urls": [source.url for source in term_sources],
        }
    except Exception as exc:  # noqa: BLE001 - produce a degraded report rather than fabricate values
        warnings.append(f"Core paper-like data unavailable: {exc}")
        core["status"] = "blocked"

    supplemental, supplemental_sources, supplemental_warnings = build_supplemental_context()
    sources.extend(supplemental_sources)
    warnings.extend(supplemental_warnings)
    unique_sources: list[SourceResult] = []
    seen_sources: set[tuple[str, str]] = set()
    for source in sources:
        key = (source.name, source.url)
        if key not in seen_sources:
            unique_sources.append(source)
            seen_sources.add(key)
    sources = unique_sources

    variants = [value for key, value in core.items() if key.endswith("_boundary_10y") or key.endswith("_boundary_20y")]
    if not variants or any(value.get("status") != "ok" for value in variants):
        overall = "数据不完整"
    elif any(value.get("extreme_components") == 2 for value in variants):
        overall = "边界"
    elif any(value.get("extreme_components") == 1 for value in variants):
        overall = "偏边界"
    else:
        overall = "中性区间"

    now = datetime.now(timezone.utc)
    return {
        "monitor": MONITOR_NAME,
        "generated_at_utc": now.isoformat(),
        "overall_state": overall,
        "research_only": True,
        "paper_url": PAPER_URL,
        "definition": {
            "core": "CAPE/dividend yield and term-spread proxy, each using 12M moving averages and rolling 10Y/20Y min-max scaling",
            "supplemental": "S&P 500 price momentum/reversal, realized volatility, VIX, Baa-minus-10Y credit spread, and curve context",
            "boundary_threshold": "A component is marked extreme at the rolling 10th or 90th percentile; this is a monitoring classification, not a trading rule",
        },
        "core": core,
        "supplemental": supplemental,
        "sources": [_source_dict(source) for source in sources],
        "warnings": warnings,
    }


def _pct(value: Any) -> str:
    value = _safe_float(value)
    return "N/A" if value is None else f"{value * 100:.2f}%"


def _num(value: Any, digits: int = 3) -> str:
    value = _safe_float(value)
    return "N/A" if value is None else f"{value:.{digits}f}"


def _core_rows(report: dict[str, Any]) -> list[tuple[str, str, str, str, str, str]]:
    rows = []
    state_labels = {"mid-regime": "中间区间", "elevated": "偏边界", "boundary": "边界"}
    for key in ("cape_boundary_10y", "cape_boundary_20y", "dividend_yield_boundary_10y", "dividend_yield_boundary_20y"):
        item = report["core"].get(key, {})
        window_years = item.get("window_years")
        window_text = f"{window_years}Y" if window_years is not None else "N/A"
        state = state_labels.get(item.get("state"), item.get("state", item.get("status", "N/A")))
        rows.append(
            (
                item.get("value_label", key),
                window_text,
                item.get("as_of", "N/A"),
                _num(item.get("valuation_ma12")),
                _num(item.get("term_spread_ma12")),
                f"{_num(item.get('boundary_score_0_to_2'))} / {item.get('extreme_components', 'N/A')} extremes / {state}",
            )
        )
    return rows


def render_text(report: dict[str, Any]) -> str:
    lines = [
        f"{report['monitor']} | {report['overall_state']}",
        f"生成时间（UTC）：{report['generated_at_utc']}",
        "研究监控，不是交易信号；任何跨资产推广都需要单独验证。",
        "",
        "核心论文式指标（12个月均值；边界分数 0—2）",
        "指标 | 窗口 | 数据月 | 估值/股息率MA12 | 期限利差MA12 | 分数/极端组件/状态",
    ]
    lines.extend(" | ".join(row) for row in _core_rows(report))
    lines.extend(
        [
            "",
            "补充状态指标",
        ]
    )
    for key, item in report.get("supplemental", {}).items():
        if key == "sp500_price_momentum":
            lines.append(
                f"S&P500 {item.get('as_of', 'N/A')}: price={_num(item.get('price'), 2)}, "
                f"1M={_pct(item.get('momentum_1m'))}, 3M={_pct(item.get('momentum_3m'))}, "
                f"6M={_pct(item.get('momentum_6m'))}, 12M={_pct(item.get('momentum_12m'))}, "
                f"20D年化波动={_pct(item.get('realized_vol_20d_annualized'))}, "
                f"近月反转={item.get('recent_reversal_flag', 'N/A')}"
            )
        else:
            lines.append(
                f"{key} {item.get('as_of', 'N/A')}: value={_num(item.get('value'), 3)}, "
                f"1周变化={_num(item.get('weekly_change'), 3)}, "
                f"1年分位={_pct(item.get('one_year_percentile'))}"
            )
    lines.extend(["", "数据来源与质量"])
    for source in report.get("sources", []):
        detail = f" | {source['detail']}" if source.get("detail") else ""
        lines.append(f"- {source['name']}: {source['status']} | as_of={source.get('as_of', 'N/A')} | {source['url']}{detail}")
    for warning in report.get("warnings", []):
        lines.append(f"- WARNING: {warning}")
    lines.extend(
        [
            "",
            "方法说明：论文的期限利差原始序列使用长期债券利率减短期国债利率；本报告优先用 FRED GS10−TB3MS，FRED 不可用时改用官方 Federal Reserve H.15 10Y−3M，并在来源与警告中明确标注 proxy/fallback。",
            f"论文链接：{PAPER_URL}",
        ]
    )
    return "\n".join(lines) + "\n"


def render_html(report: dict[str, Any]) -> str:
    rows = "".join(
        "<tr>" + "".join(f"<td>{escape(str(cell))}</td>" for cell in row) + "</tr>" for row in _core_rows(report)
    )
    supplemental_rows: list[str] = []
    for key, item in report.get("supplemental", {}).items():
        if key == "sp500_price_momentum":
            text = (
                f"price={_num(item.get('price'), 2)}; 1M={_pct(item.get('momentum_1m'))}; "
                f"3M={_pct(item.get('momentum_3m'))}; 6M={_pct(item.get('momentum_6m'))}; "
                f"12M={_pct(item.get('momentum_12m'))}; 20D年化波动={_pct(item.get('realized_vol_20d_annualized'))}; "
                f"近月反转={item.get('recent_reversal_flag', 'N/A')}"
            )
        else:
            text = f"value={_num(item.get('value'))}; 1周变化={_num(item.get('weekly_change'))}; 1年分位={_pct(item.get('one_year_percentile'))}"
        supplemental_rows.append(f"<tr><td>{escape(key)}</td><td>{escape(str(item.get('as_of', 'N/A')))}</td><td>{escape(text)}</td></tr>")
    warnings = "".join(f"<li>{escape(warning)}</li>" for warning in report.get("warnings", [])) or "<li>无</li>"
    sources = "".join(
        f"<li>{escape(source['name'])}: {escape(str(source['as_of']))} — <a href=\"{escape(source['url'])}\">{escape(source['url'])}</a></li>"
        for source in report.get("sources", [])
    )
    return f"""<!doctype html>
<html><body style="font-family:Arial,sans-serif;color:#222">
<h2>{escape(report['monitor'])} | {escape(report['overall_state'])}</h2>
<p>生成时间（UTC）：{escape(report['generated_at_utc'])}<br>
研究监控，不是交易信号；跨资产推广需要单独验证。</p>
<h3>核心论文式指标</h3>
<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">
<thead><tr><th>指标</th><th>窗口</th><th>数据月</th><th>估值/股息率 MA12</th><th>期限利差 MA12</th><th>分数 / 极端组件 / 状态</th></tr></thead>
<tbody>{rows}</tbody></table>
<h3>补充状态指标</h3>
<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">
<thead><tr><th>指标</th><th>日期</th><th>读数</th></tr></thead><tbody>{''.join(supplemental_rows)}</tbody></table>
<h3>数据质量</h3><ul>{warnings}</ul><ul>{sources}</ul>
<p>期限利差核心口径优先采用 FRED GS10−TB3MS；FRED 不可用时采用官方 Federal Reserve H.15 10Y−3M fallback，并在来源与警告中披露。论文原始口径为长期债券利率减短期国债利率。<br>
<a href="{PAPER_URL}">论文：Boundaries of Time-Series Momentum</a></p>
</body></html>"""


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=MONITOR_NAME)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/equity_momentum_boundary"))
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report = build_report()
    text_body = render_text(report)
    html_body = render_html(report)
    (args.output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output_dir / "report.txt").write_text(text_body, encoding="utf-8")
    (args.output_dir / "report.html").write_text(html_body, encoding="utf-8")
    (args.output_dir / "metadata.json").write_text(
        json.dumps(
            {
                "subject": f"每周股票动量边界监控 | {report['overall_state']} | {report['generated_at_utc'][:10]}",
                "body": text_body,
                "html_body": html_body,
                "attachment": None,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(text_body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
