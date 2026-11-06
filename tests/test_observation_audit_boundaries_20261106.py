"""Independent consumer audit regressions; synthetic envelopes, no sends or market I/O."""
import copy
import json
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ic_im_intraday_observations as obs
import build_ic_im_v1_4_digest as digest
from test_icim_intraday_observations_20261106 import payload as source_fixture


def envelope():
    p = source_fixture.__wrapped__()
    p["product_observations"]["IM"]["signal"].update(
        next_trade_date="2026-11-09", future_quote_date="2026-11-06", core_current="IM2612",
        future_quote_source="synthetic", future_last=8000., future_quote_evidence=dict(
            contract="IM2612", source="synthetic", source_date="2026-11-06",
            row_source_date="2026-11-06", lastprice=8000., collected_at="2026-11-06T14:30:30+08:00",
            source_snapshot_at="2026-11-06T14:30:00+08:00", source_snapshot_kind="latest_available_contract_update"))
    return p


def test_partial_marker_removal_cannot_promote_missing_protection_to_verified():
    p = envelope()
    row = p["product_observations"]["IM"]
    row["status"] = "verified"
    row["signal"].pop("calendar_partial_revision")
    row["signal"]["calendar_provisional"] = False
    with pytest.raises(ValueError, match="cannot be relabelled verified"):
        obs.build(p)


@pytest.mark.parametrize("field", ["signal_complete", "persistence_eligible", "futures_target_confirmed"])
def test_verified_status_does_not_override_false_qualification(field):
    p = envelope(); s = p["product_observations"]["IM"]["signal"]
    p["product_observations"]["IM"]["status"] = "verified"
    s.pop("calendar_partial_revision");s["calendar_provisional"] = False
    s.update(signal_complete=True, persistence_eligible=True, futures_target_confirmed=True,
             unconfirmed_put_legs=[], incomplete_components=[])
    s[field] = False
    with pytest.raises(ValueError): obs.validate(p)


@pytest.mark.parametrize("changes", [
    {"generated_at": "2099-01-01T14:30:00+08:00", "report_generated_at": "2099-01-01T14:31:00+08:00"},
    {"report_generated_at": "2026-11-05T14:31:00+08:00"},
    {"generated_at": "2026-11-06T14:32:00+08:00"},
    {"generated_at": "2026-11-05T14:30:00+08:00"},
    {"generated_at": "2026-11-06T15:00:00+08:00", "report_generated_at": "2026-11-06T15:01:00+08:00"},
    {"generated_at": "2026-11-06T14:30:00"},
    {"report_generated_at": "2026-11-06T14:31:00"},
])
def test_future_reversed_wrongday_or_cutoff_query_clocks_are_rejected(changes):
    p = envelope();p.update(changes)
    with pytest.raises(ValueError):obs.validate(p)


def test_signal_completion_cannot_be_after_its_report():
    p = envelope();p["product_observations"]["IM"]["signal"]["signal_generated_at"] = "2026-11-06T14:32:00+08:00"
    with pytest.raises(ValueError):obs.validate(p)


def test_saved_rerender_may_report_after_close_but_preserves_original_query_clock():
    p = envelope();before = copy.deepcopy(p)
    p.update(saved_diagnostic_rerender=True, signal_timing="saved_intraday_diagnostic_rerender",
             report_generated_at="2026-11-06T15:20:00+08:00")
    title, text, _ = obs.build(p)
    assert "补充整理" in title and "不具有新的盘中发布资格" in text
    assert p["generated_at"] == before["generated_at"]


@pytest.mark.parametrize("explicit_report", [False, True])
def test_real_digest_cli_routes_observation_body_html_and_attachment_without_signals(monkeypatch, tmp_path, explicit_report):
    p = envelope()
    from check_ic_im_v1_4_delivery import expected_identity_for_day
    from datetime import date
    build, delivery = expected_identity_for_day(date(2026, 11, 6))
    p.update(build=build, delivery_revision=delivery, report_file="observations.md")
    assert "signals" not in p and "strategy_revision" not in p
    source_report = tmp_path / "observations.md"
    source_report.write_text("read-only diagnostic original; no new quotes", encoding="utf-8")
    source = tmp_path / "result.json";source.write_text(json.dumps(p), encoding="utf-8")
    out = tmp_path / "rendered"
    args = ["digest", "--result", str(source), "--out-dir", str(out)]
    if explicit_report:args += ["--report", str(source_report)]
    monkeypatch.setattr(sys, "argv", args)
    provenance_calls = []
    original = digest.source_provenance
    def provenance(*args):
        provenance_calls.append(args)
        return original(*args)
    monkeypatch.setattr(digest, "source_provenance", provenance)
    monkeypatch.setattr(digest, "build_failure", lambda *a, **k: pytest.fail("valid observations must not become failure mail"))
    assert digest.main() == 0
    assert provenance_calls == [("", "")]
    meta = json.loads((out / "metadata.json").read_text(encoding="utf-8"))
    assert "盘中信息" in meta["subject"] and "0→0.25" in meta["body"]
    assert "保护未落实" in meta["body"] and "需要0.75张" in meta["body"]
    assert "&lt;script&gt;" in meta["html_body"] and "<script>" not in meta["html_body"]
    assert Path(meta["attachment"]).resolve() == source_report.resolve()
    assert json.loads(source.read_text()) == p
    assert sorted(f.name for f in out.iterdir()) == ["ic_im_v1_4_digest.html", "ic_im_v1_4_digest.md", "metadata.json"]


def test_new_source_proof_uses_release_specific_exact_manifest(monkeypatch, tmp_path):
    import hashlib
    from datetime import datetime, timezone
    root_manifest = json.loads((ROOT / "docs/ic_im_daily_release_20261023_repairs_v3_r3_manifest.json").read_text())
    root_manifest["release"] = "20261106-intraday-observations-v1"
    path = tmp_path / "docs/ic_im_intraday_observation_20261106_manifest.json"
    path.parent.mkdir()
    raw = json.dumps(root_manifest).encode();path.write_bytes(raw)
    monkeypatch.setattr(digest, "__file__", str(tmp_path / "scripts/build_ic_im_v1_4_digest.py"))
    proof = dict(status="verified", release=root_manifest["release"], strategy_sha="a" * 40,
                 manifest_sha256=hashlib.sha256(raw).hexdigest(), source_sha256=root_manifest["files"],
                 verified_at=datetime.now(timezone.utc).isoformat())
    source = tmp_path / "source.json";source.write_text(json.dumps(proof))
    assert digest.source_provenance(str(source), "a" * 40) == proof
    path.write_bytes(raw + b"\n")
    with pytest.raises(ValueError, match="execution inventory"):
        digest.source_provenance(str(source), "a" * 40)
    path.write_bytes(raw)
    proof["release"] = "../../unreviewed-manifest"
    source.write_text(json.dumps(proof))
    with pytest.raises(ValueError, match="execution inventory"):
        digest.source_provenance(str(source), "a" * 40)


def test_information_envelope_still_cannot_create_normal_delivery_marker():
    import prepare_ic_im_v1_4_marker as marker
    p = envelope()
    with pytest.raises(ValueError):
        marker.marker_name(p)
    p["saved_diagnostic_rerender"] = True
    with pytest.raises(ValueError):
        marker.marker_name(p)


@pytest.mark.parametrize("field,value", [
    ("next_trade_date", "2026-11-10"), ("future_quote_date", "2026-11-05"),
    ("future_quote_source", "another source"), ("future_last", 8001.),
    ("core_current", "IM2703"), ("future_quote_evidence", None),
])
def test_qualified_future_date_identity_price_requires_bound_evidence(field, value):
    p = envelope();p["product_observations"]["IM"]["signal"][field] = value
    with pytest.raises(ValueError):obs.validate(p)


@pytest.mark.parametrize("field,value", [
    ("source_date", "2026-11-05"), ("row_source_date", "2026-11-05"),
    ("source_snapshot_at", "2026-11-05T14:30:00+08:00"),
    ("source_snapshot_at", "2026-11-06T14:30:31+08:00"),
    ("collected_at", "2026-11-06T15:00:00+08:00"),
    ("collected_at", "2026-11-06T14:32:00+08:00"),
    ("collected_at", "2026-11-06T14:30:00"),
    ("source_snapshot_kind", "unverified"), ("lastprice", 0),
])
def test_partial_future_evidence_cannot_forge_dates_or_clocks(field, value):
    p = envelope();p["product_observations"]["IM"]["signal"]["future_quote_evidence"][field] = value
    with pytest.raises(ValueError):obs.validate(p)


def test_verified_positive_put_contract_must_not_be_unknown():
    from check_ic_im_v1_4_delivery import expected_identity_for_day
    from datetime import date
    p = envelope();row = p["product_observations"]["IM"];s = row["signal"]
    build, delivery = expected_identity_for_day(date(2026,11,6))
    p.update(build=build, delivery_revision=delivery)
    row["status"] = "verified";s.pop("calendar_partial_revision")
    s.update(calendar_provisional=False, signal_complete=True, persistence_eligible=True,
             unconfirmed_put_legs=[], incomplete_components=[], strategy_version="1.4", strategy_revision="r1",
             v14_build_id=build, core_put_current_qty_normalized=1.5, core_put_target_qty_normalized=1.5,
             core_put_target_contract="MO2612-P-6700", momentum_put_current_qty_normalized=0.,
             momentum_put_current_contract=None, momentum_put_target_qty_normalized=.75,
             momentum_put_target_contract="MO2612-P-6700", total_put_target_qty_normalized=2.25)
    obs.validate(p)
    for field in ("core_put_current_contract", "core_put_target_contract", "momentum_put_target_contract"):
        changed = copy.deepcopy(p);changed["product_observations"]["IM"]["signal"][field] = None
        with pytest.raises(ValueError, match="positive Put contract"):
            obs.validate(changed)
