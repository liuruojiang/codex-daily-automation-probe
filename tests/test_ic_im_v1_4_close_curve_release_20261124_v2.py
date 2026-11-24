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

OLD = "docs/ic_im_report_index_basis_20261124_v1_manifest.json"
NEW = "docs/ic_im_report_close_curve_20261124_v2_manifest.json"
RELEASE = "20261124-report-close-curve-v2"
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
        "ic_im_quarter_roll_v1_3.py", "poe_ic_im_mainline_v1_4_bot.py"}
    for key in ("support_files", "historical_exclusion_registry_sha256",
                "daily_gate_deselections", "daily_gate_outer_timeout_seconds"):
        assert new[key] == old[key]
    assert new["required_local_tests"] == old["required_local_tests"] + [
        "test_icim_close_curve_20261124_v2.py"]
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


def curve_signal(product, day="2026-12-18", *, spot=6100.):
    if day == "2026-12-18":
        months, expiries = ("2701", "2702", "2703", "2706"), ("2027-01-15", "2027-02-19", "2027-03-19", "2027-06-18")
        listed = [product + m for m in ("2612", "2701", "2703", "2706")]
        current_expiry = "2026-12-18"
    elif day == "2026-10-09":
        months, expiries = ("2610", "2611", "2612", "2703"), ("2026-10-16", "2026-11-20", "2026-12-18", "2027-03-19")
        listed = [product + m for m in months]
        current_expiry = "2026-10-16"
    else:
        months, expiries = ("2612", "2701", "2703", "2706"), ("2026-12-18", "2027-01-15", "2027-03-19", "2027-06-18")
        listed = [product + m for m in months]
        current_expiry = "2026-11-20"
    legs = {role: dict(status="ok", contract=product + month, price=price, expiry=expiry)
            for role, month, price, expiry in zip(digest.CLOSE_CURVE_ROLES, months,
                (6000., 6001., 5800., 5700.), expiries)}
    return dict(product=product, mode="close", market_date=day, close_confirmed=True,
                index_price=spot, future_quote_date=day, future_quote_source="fixture",
                close_report_curve=dict(schema="close_curve_v2", product=product, market_date=day,
                    source="fixture", source_date=day, price_type="same_snapshot_lastprice",
                    listed_contracts=listed, current_month_expiry=current_expiry, legs=legs,
                    index=dict(status="ok", price=spot, source_date=day, source="fixture index close")))


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_expiry_day_excludes_expiring_month_even_if_quote_in_listing(product):
    signal = curve_signal(product)
    lines = digest.spread_reasons(signal, product)
    assert len(lines) == 8
    assert ["N/A" in line for line in lines[:7]] == [True, False, False, False, True, False, False]
    assert all(product + "2612" not in line for line in lines)
    assert all(product + "2701" in line for line in lines[1:4])
    assert "正值为贴水" in lines[-1] and "不是结算价" in lines[-1]


def test_independent_quotes_and_bounded_display():
    signal = curve_signal("IC", "2026-11-25")
    for role in digest.CLOSE_CURVE_ROLES[1:]:
        signal["close_report_curve"]["legs"][role]["status"] = "unavailable"
    assert ["N/A" in line for line in digest.format_close_curve(signal)[3:7]] == [False, True, True, True]
    signal = curve_signal("IC", "2026-11-25")
    signal["close_report_curve"]["legs"]["near"]["status"] = "unavailable"
    assert ["N/A" in line for line in digest.format_close_curve(signal)[3:7]] == [True, False, False, False]
    signal = curve_signal("IC", "2026-11-25", spot=1e308)
    assert all("N/A" in line for line in digest.format_close_curve(signal)[3:7])
    assert max(map(len, digest.format_close_curve(signal))) < 300


def test_normal_email_markdown_and_html_use_same_curve_after_real_delivery_validation(monkeypatch):
    from datetime import date
    from html import unescape
    from test_ic_im_v1_4_fear_sources import payload
    # Use the existing valid, fully validated historical delivery fixture.
    # Backdate only the rendering feature flag inside this isolated test;
    # production's effective date and all delivery/identity gates are unchanged.
    data = payload(monkeypatch)
    monkeypatch.setattr(digest, "CLOSE_CURVE_EFFECTIVE", date(2026, 10, 9))
    for p, signal in data["signals"].items():
        signal.update(curve_signal(p, "2026-10-09", spot=signal.get("index_price", 6100.)))
    digest.validate_success_payload(data)
    subject, markdown, _ = digest.build_success(data, "", "")
    rendered_html = unescape(digest.build_success_html(data, ""))
    for p, signal in data["signals"].items():
        expected = digest.format_close_curve(signal, product=p)
        assert len(expected) == 8 and sum("N/A" not in line for line in expected[:7]) >= 5
        for line in expected:
            assert line in markdown and line in rendered_html


def test_failure_markdown_html_retain_independent_quotes_as_observations():
    from html import unescape
    signal = curve_signal("IC")
    expected = digest.format_close_curve(signal)
    native = "### IC / 中证500\n\n" + "\n".join("- " + line + "（仅行情观察）" for line in expected)
    markdown, rendered = digest.failure_diagnostic_display(native)
    for line in expected:
        assert line in markdown and line in unescape(rendered)
    assert "整体信号尚未确认" in markdown
