"""Independent report-rendering checks for the three disjoint listed tenors."""
from copy import deepcopy
from datetime import date
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_ic_im_v1_4_digest as digest


KEYS = ("near_far_month_spread", "near_near_quarter_spread", "near_far_quarter_spread")
LABELS = ("近月－远月", "近月－近季", "近月－远季")


def leg(product, near, far, market, current_expiry, near_expiry, far_expiry, label, near_price=6000., far_price=5900.):
    gap = (date.fromisoformat(far_expiry) - date.fromisoformat(near_expiry)).days
    spread = near_price - far_price
    return dict(status="ok", label=label, convention="near_minus_far", source="CFFEX", source_date=market,
                price_type="same_snapshot_lastprice", current_month_expiry=current_expiry,
                near_contract=product+near, far_contract=product+far, near_expiry=near_expiry,
                far_expiry=far_expiry, expiry_gap_days=gap, near_price=near_price, far_price=far_price,
                points=spread, ratio=spread/near_price, annualized=spread/near_price*365/gap,
                same_contract=False)


def signal(product="IM", after=False):
    market = "2026-11-23" if after else "2026-11-20"
    near, near_expiry = ("2612", "2026-12-18") if after else ("2611", "2026-11-20")
    fars = (("2701", "2027-01-15"), ("2703", "2027-03-19"), ("2706", "2027-06-18")) if after else (
        ("2612", "2026-12-18"), ("2703", "2027-03-19"), ("2706", "2027-06-18"))
    return {"market_date": market, "product": product,
            **{key: leg(product, near, far, market, "2026-11-20", near_expiry, exp,
                        label, far_price=6000.-100*i)
               for i, (key, label, (far, exp)) in enumerate(zip(KEYS, LABELS, fars), 1)}}


@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("after", [False, True])
def test_all_three_visible_pairs_use_same_baseline_in_text_and_html(product, after):
    value = signal(product, after)
    before = deepcopy(value)
    lines = digest.spread_reasons(value, product)
    assert len(lines) == 3
    for key, label, line in zip(KEYS, LABELS, lines):
        row = value[key]
        assert line.startswith(f"期限贴水（{label}）：{row['near_contract']} ")
        assert row["far_contract"] in line
        assert f"{row['points']:+.2f}点" in line
        assert f"{row['annualized']:+.2%}/年" in line
        assert line in digest.product_reasons(product, value)
        assert line in digest.reasons_html(product, value)
    assert value == before


def test_post_cutover_missing_never_falls_back_to_legacy_pairs():
    value = {"market_date": "2026-11-20", "quarter_spread": {"status": "ok"},
             "month_next_spread": {"status": "ok"}, "month_quarter_spread": {"status": "ok"}}
    lines = digest.spread_reasons(value, "IM")
    assert len(lines) == 3 and all("N/A" in line for line in lines)
    assert all(label in line for label, line in zip(LABELS, lines))
    assert "近季－远季" not in "\n".join(lines)


def test_historical_before_cutover_preserves_old_rendering():
    lines = digest.spread_reasons({"market_date": "2026-11-19"}, "IM")
    assert len(lines) == 3 and all("N/A" in line for line in lines)
    assert "季月价差" in lines[0]
    assert "当月－次月" in lines[1] and "当月－当季" in lines[2]


@pytest.mark.parametrize("field,value", [
    ("status", "unavailable"), ("source_date", "2026-11-19"), ("near_contract", "IC2611"),
    ("far_contract", "IM2707"), ("far_contract", "IM2703"), ("near_price", True),
    ("far_price", None), ("far_price", 0), ("far_price", -1), ("points", float("inf")),
    ("ratio", .45), ("annualized", -.45), ("annualized", float("nan")),
    ("expiry_gap_days", 1), ("near_expiry", "2026-10-16"), ("far_expiry", "2027-07-18"),
    ("same_contract", True), ("current_month_expiry", "2026-10-16"), ("source", ""),
    ("source", None),
])
def test_one_bad_far_leg_does_not_erase_other_two(field, value):
    s = signal()
    s[KEYS[2]][field] = value
    lines = digest.spread_reasons(s, "IM")
    assert "N/A" not in lines[0] and "N/A" not in lines[1]
    assert "N/A" in lines[2] and "IM2707" not in lines[2]


@pytest.mark.parametrize("field", ["source", "source_date", "near_contract", "far_contract", "near_price",
    "far_price", "points", "ratio", "annualized", "near_expiry", "far_expiry", "expiry_gap_days",
    "current_month_expiry", "same_contract"])
def test_missing_metadata_rejects_only_affected_leg(field):
    s = signal()
    del s[KEYS[1]][field]
    lines = digest.spread_reasons(s, "IM")
    assert "N/A" in lines[1] and all("N/A" not in lines[i] for i in (0, 2))


def test_common_baseline_mismatch_rejects_all_legs():
    s = signal()
    bad = s[KEYS[2]]
    bad.update(near_price=6100., points=400., ratio=400/6100, annualized=400/6100*365/bad["expiry_gap_days"])
    assert all("N/A" in x for x in digest.spread_reasons(s, "IM"))


def test_common_source_mismatch_rejects_all_legs():
    s = signal()
    s[KEYS[0]]["source"] = "different snapshot"
    assert all("N/A" in x for x in digest.spread_reasons(s, "IM"))


@pytest.mark.parametrize("field,value", [("label", "近季－远季"), ("convention", "far_minus_near"),
                                           ("price_type", "settlement")])
def test_conflicting_declared_semantics_are_not_silently_reinterpreted(field, value):
    s = signal()
    s[KEYS[1]][field] = value
    assert "N/A" in digest.spread_reasons(s, "IM")[1]


def test_self_consistent_wrong_expiry_is_not_displayed_as_a_valid_annualization():
    s = signal()
    bad = leg("IM", "2611", "2706", "2026-11-20", "2026-11-20", "2026-11-20",
              "2027-06-01", LABELS[2], far_price=5700.)
    s[KEYS[2]] = bad
    assert "N/A" in digest.spread_reasons(s, "IM")[2]


def test_wrong_front_month_cannot_be_justified_by_fabricated_expiry():
    s = signal(after=True)
    s["market_date"] = "2026-11-20"
    for key in KEYS:
        s[key].update(source_date="2026-11-20", current_month_expiry="2026-11-01")
    assert all("N/A" in x for x in digest.spread_reasons(s, "IM"))


def test_missing_signal_date_cannot_be_replaced_by_claimed_source_date():
    s = signal()
    del s["market_date"]
    assert all("N/A" in x for x in digest.spread_reasons(s, "IM"))


@pytest.mark.parametrize("weekend", ["2026-11-21", "2026-11-22"])
def test_weekend_before_new_listing_is_never_presented_as_market_session(weekend):
    s = signal(after=True)
    s["market_date"] = weekend
    for key in KEYS:
        s[key]["source_date"] = weekend
    assert all("N/A" in x for x in digest.spread_reasons(s, "IM"))


def test_finite_input_whose_display_percentage_overflows_is_unavailable():
    s = signal()
    s[KEYS[0]] = leg("IM", "2611", "2612", "2026-11-20", "2026-11-20", "2026-11-20",
                       "2026-12-18", LABELS[0], near_price=1., far_price=4e305)
    # Isolate the offending leg from the common-price consistency check.
    s[KEYS[1]]["status"] = s[KEYS[2]]["status"] = "unavailable"
    line = digest.spread_reasons(s, "IM")[0]
    assert "N/A" in line and "inf%" not in line
