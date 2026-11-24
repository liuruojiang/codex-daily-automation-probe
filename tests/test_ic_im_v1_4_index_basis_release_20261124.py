"""Exact release inventory and source-proof compatibility for the dated repair."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_ic_im_v1_4_digest as digest
import verify_icim_source_release as verifier

OLD = "docs/ic_im_previous_session_master_20261123_v1_manifest.json"
NEW = "docs/ic_im_report_index_basis_20261124_v1_manifest.json"
RELEASE = "20261124-report-index-basis-v1"
COMMIT = "b" * 40


def proof(name):
    raw = (ROOT / name).read_bytes()
    m = json.loads(raw)
    return {"status": "verified", "release": m["release"], "strategy_sha": COMMIT,
            "manifest_sha256": hashlib.sha256(raw).hexdigest(), "source_sha256": m["files"],
            "support_sha256": m["support_files"], "verified_at": datetime.now(timezone.utc).isoformat()}


def test_new_release_changes_only_exact_reviewed_inventory():
    old, new = (json.loads((ROOT / name).read_bytes()) for name in (OLD, NEW))
    assert new["release"] == RELEASE and new["files"].keys() == old["files"].keys()
    assert {k for k in new["files"] if new["files"][k] != old["files"][k]} == {
        "ic_im_quarter_roll_v1_3.py"}
    for key in ("support_files", "historical_exclusion_registry_sha256",
                "daily_gate_deselections", "daily_gate_outer_timeout_seconds"):
        assert new[key] == old[key]
    assert new["required_local_tests"] == old["required_local_tests"] + [
        "test_icim_index_basis_20261124.py"]
    assert all(new["fixture_sha256"][k] == v for k, v in old["fixture_sha256"].items())


def test_current_proof_cannot_borrow_old_manifest_or_drop_support(tmp_path):
    path = tmp_path / "proof.json"
    current = proof(NEW)
    path.write_text(json.dumps(current))
    assert digest.source_provenance(str(path), COMMIT, RELEASE) == current
    for bad in (proof(OLD), {**current, "support_sha256": {}},
                {**current, "manifest_sha256": "0" * 64}):
        path.write_text(json.dumps(bad))
        with pytest.raises(ValueError):
            digest.source_provenance(str(path), COMMIT, RELEASE)


def test_missing_new_manifest_refuses_old_fallback(tmp_path, monkeypatch):
    old_path = tmp_path / OLD
    old_path.parent.mkdir(parents=True)
    old_path.write_bytes((ROOT / OLD).read_bytes())
    def git(args, **kwargs):
        if args[-2:] == ["rev-parse", "HEAD"]:
            return COMMIT + "\n"
        if "ls-tree" in args:
            return NEW + "\n" + OLD + "\n"
        pytest.fail("new pinned manifest must reject before old read")
    monkeypatch.setattr(verifier.subprocess, "check_output", git)
    with pytest.raises(ValueError, match="pinned release manifest is missing"):
        verifier.verify(tmp_path, COMMIT)


def close_signal(product="IC", day="2026-11-25", spot=6100.0):
    signal = {"product": product, "market_date": day, "close_confirmed": True,
              "index_price": spot, "future_quote_date": day, "future_quote_source": "fixture"}
    front, front_price, front_expiry = product + "2612", 6000.0, "2026-12-18"
    for key, label, month, price, expiry, gap in zip(
            digest.TENOR_SPREAD_LABELS, digest.TENOR_SPREAD_LABELS.values(),
            ("2701", "2703", "2706"), (6001., 5800., 5700.),
            ("2027-01-15", "2027-03-19", "2027-06-18"), (28, 91, 182)):
        points = front_price - price
        signal[key] = {"status": "ok", "source": "fixture", "source_date": day,
                       "near_contract": front, "far_contract": product + month,
                       "near_price": front_price, "far_price": price, "points": points,
                       "ratio": points / front_price, "annualized": points / front_price * 365 / gap,
                       "same_contract": False, "near_expiry": front_expiry, "far_expiry": expiry,
                       "expiry_gap_days": gap, "current_month_expiry": "2026-11-20", "label": label,
                       "convention": "near_minus_far", "price_type": "same_snapshot_lastprice"}
    return signal


@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("spot", [6100., 5900.])
def test_normal_digest_has_all_seven_spreads(product, spot):
    from datetime import date
    signal = close_signal(product, spot=spot)
    lines = digest.spread_reasons(signal, product)
    assert len(lines) == 7 and all("N/A" not in line for line in lines)
    assert lines[:3] == digest.tenor_spread_reasons(signal, product)
    for line, label, contract, price, expiry in zip(
            lines[3:], digest.INDEX_BASIS_LABELS, ("2612", "2701", "2703", "2706"),
            (6000., 6001., 5800., 5700.), ("2026-12-18", "2027-01-15", "2027-03-19", "2027-06-18")):
        points = spot - price
        ratio = points / spot
        days = (date.fromisoformat(expiry) - date.fromisoformat(signal["market_date"])).days
        assert label in line and product + contract in line
        assert f"{points:+.2f}点" in line and f"{ratio:+.2%}" in line
        assert f"{ratio * 365 / days:+.2%}/年" in line


def test_prior_and_intraday_do_not_gain_new_lines():
    for signal in (close_signal(day="2026-11-23"),
                   {**close_signal(), "close_confirmed": False}):
        assert len(digest.spread_reasons(signal, "IC")) == 3


def test_invalid_and_missing_evidence_does_not_mix_contracts():
    signal = close_signal()
    del signal["near_far_month_spread"]
    lines = digest.format_index_basis(signal)
    assert ["N/A" in line for line in lines] == [False, True, False, False]
    signal["future_quote_date"] = "2026-11-24"
    assert all("N/A" in line for line in digest.format_index_basis(signal))
