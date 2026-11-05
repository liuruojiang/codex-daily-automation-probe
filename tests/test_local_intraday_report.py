"""Saved real result and executable faults; no fetch, ledger or send operations."""
import csv
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_local_intraday_report as renderer

FIXTURE = ROOT / "tests/fixtures/local_intraday_20261105"


@pytest.fixture
def inputs(tmp_path):
    for path in FIXTURE.iterdir():
        shutil.copyfile(path, tmp_path / path.name)
    return dict(mode="replay", signal_day="2026-11-05", anchor_day="2026-11-04", next_session="2026-11-06",
                icim_result=tmp_path / "icim_result.json", microcap_readiness=tmp_path / "readiness_projection.json",
                microcap_csv={v: tmp_path / f"microcap_top100_mom16_biweekly_live_{v.replace('.', '_')}_realtime_signal.csv"
                              for v in renderer.VERSIONS})


def alter_json(path, fn):
    d = json.loads(path.read_bytes()); fn(d)
    path.write_text(json.dumps(d, ensure_ascii=False), encoding="utf8")


def alter_csv(path, key, value):
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f); names = reader.fieldnames; rows = list(reader)
    rows[0][key] = value
    with path.open("w", encoding="utf8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=names); writer.writeheader(); writer.writerows(rows)


def test_fixture_provenance_is_exact():
    p = json.loads((FIXTURE / "provenance.json").read_bytes())
    for entry in p["entries"]:
        assert hashlib.sha256((FIXTURE / entry["file"]).read_bytes()).hexdigest() == entry["sha256"]


def test_real_saved_results_render_units_and_full_evidence_without_mutation(inputs):
    result = renderer.render(**inputs)
    assert "6.500元（按合约编码）" in result["summary"]
    assert "6700点（按合约编码）" in result["summary"]
    assert "行权价6500" not in result["report"]
    assert "15→15" in result["summary"] and "1.5→1.5" in result["summary"]
    assert "Put 档位、期货对应数量与行权价" in result["report"]
    assert "2026-11-06" in result["summary"] and "49.79" in result["summary"]
    assert "历史盘中快照只读重渲染" in result["summary"]
    manifest = result["manifest"]
    assert manifest["actual_publication"] is manifest["smtp_invoked"] is manifest["production_authorized"] is False
    assert manifest["needs_actual_publication"] is False
    for path, digest in manifest["inputs_sha256"].items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("family", ["icim", "microcap"])
def test_independent_strategy_can_render_first(inputs, family):
    if family == "icim":
        inputs.update(microcap_csv=None, microcap_readiness=None)
    else:
        inputs["icim_result"] = None
    result = renderer.render(**inputs)
    assert len(result["manifest"]["rows"]) == (2 if family == "icim" else 3)


@pytest.mark.parametrize("key,value", [("date", "2026-11-04"), ("quote_trade_date", "2026-11-04"),
    ("latest_anchor_trade_date", "2026-11-03"), ("member_count", "99"), ("quote_coverage", "99/100"),
    ("member_quote_integrity_count", "99"), ("member_quote_bad_symbols", '["000001"]'),
    ("member_quote_integrity_bad_symbols", '["000001"]'), ("next_session_actionable_scale", "1"),
    ("strategy_revision", "old"), ("snapshot_time", "2026-11-05T15:00:00+08:00"),
    ("snapshot_time", "2026-11-05T14:45:00"), ("notice_audit_manifest_sha256", "f" * 64)])
def test_microcap_faults_fail_closed(inputs, key, value):
    alter_csv(inputs["microcap_csv"]["v2.0"], key, value)
    with pytest.raises(ValueError): renderer.render(**inputs)


@pytest.mark.parametrize("fault", ["duplicate", "st", "stage", "old_day", "notice_gap"])
def test_readiness_member_st_and_gate_faults(inputs, fault):
    def change(d):
        pre = d["stages"][0]["report"]
        if fault == "duplicate": pre["current_member_symbols"][1] = pre["current_member_symbols"][0]
        elif fault == "st": pre["refresh_proof"]["preflight_evidence"]["current_st_name_intersection"] = 1
        elif fault == "stage": d["stages"][2]["exit_code"] = 1
        elif fault == "old_day": d["expected_date"] = "2026-11-03"
        else: pre["refresh_proof"]["preflight_evidence"]["current_member_notice_audit"]["symbols"] = []
    alter_json(inputs["microcap_readiness"], change)
    with pytest.raises(ValueError): renderer.render(**inputs)


def test_missing_quote_remains_na_no_other_contract_fill(inputs):
    def change(d):
        d["signals"]["IC"].pop("put_market", None)
        d["signals"]["IC"].pop("put_pricing_evidence", None)
    alter_json(inputs["icim_result"], change)
    result = renderer.render(**inputs)
    assert "N/A（result未保存该合约报价原文）" in result["report"]
    assert "6.500元" in result["summary"]  # Identity is independent of absent mark evidence.


def test_cash_to_long_action_is_derived_not_hardcoded_hold(inputs):
    p = inputs["microcap_csv"]["v2.0"]
    alter_csv(p, "next_holding", "long_microcap_short_zz1000")
    alter_csv(p, "next_session_actionable_scale", "1")
    result = renderer.render(**inputs)
    row = next(r for r in result["manifest"]["rows"] if r["strategy"] == "v2.0")
    assert row["target"] == "long_microcap_short_zz1000" and row["scale_target"] == "1"
    assert row["action"] != "无操作"


def test_live_after_cutoff_refuses_microcap_but_keeps_icim_independent(inputs, monkeypatch):
    monkeypatch.setattr(renderer, "now_bj", lambda: datetime.fromisoformat("2026-11-05T15:02:00+08:00"))
    inputs["mode"] = "intraday"
    with pytest.raises(ValueError, match="before 15:00"): renderer.render(**inputs)
    inputs.update(microcap_csv=None, microcap_readiness=None)
    result = renderer.render(**inputs)
    assert result["manifest"]["needs_actual_publication"] is True
    assert result["manifest"]["actual_publication"] is False


def test_live_same_day_and_precise_next_session(inputs, monkeypatch):
    monkeypatch.setattr(renderer, "now_bj", lambda: datetime.fromisoformat("2026-11-06T14:30:00+08:00"))
    inputs["mode"] = "intraday"
    with pytest.raises(ValueError, match="today"): renderer.render(**inputs)
    inputs.update(mode="replay", next_session="2026-11-09")
    with pytest.raises(ValueError, match="next actual"): renderer.render(**inputs)


def test_original_module_clock_never_patched_by_replay(inputs):
    old_clock = renderer.micro.now_bj
    renderer.render(**inputs)
    assert renderer.micro.now_bj is old_clock


def test_icim_wrong_day_or_revision_cannot_render(inputs):
    alter_json(inputs["icim_result"], lambda d: d["signals"]["IC"].update(v14_rule_revision="wrong"))
    with pytest.raises(ValueError): renderer.render(**inputs)


def test_input_changed_while_rendering_fails(inputs, monkeypatch):
    original = renderer.icim.build_success
    def change(*args, **kwargs):
        result = original(*args, **kwargs)
        inputs["microcap_csv"]["v2.0"].write_bytes(b"tampered")
        return result
    monkeypatch.setattr(renderer.icim, "build_success", change)
    with pytest.raises(ValueError, match="input changed"): renderer.render(**inputs)


def test_partial_fear_never_becomes_zero_or_hold_target(inputs):
    def change(d):
        for s in d["signals"].values():
            s.update(fear_greed_index=None, fear_data_date=None, fear_data_status="unavailable",
                     fear_csv_observations=None, fear_csv_latest_date=None, fear_csv_latest_value=None,
                     fear_page_value=None, fear_source_url=None, fear_csv_sha256=None, fear_page_sha256=None,
                     fear_page_updated_at=None, fear_page_data_date=None, fear_latest_available_date=None,
                     fear_available_sources=[], fear_selected_source=None, fear_cross_validation="unavailable",
                     fear_source_warning="百分位没有合格读数", fear_unavailable_reason="没有合格读数",
                     signal_completeness="partial", grid_target_confirmed=False, total_units_target_confirmed=False,
                     incomplete_components=["grid", "total_futures"], reference_grid_target=s["grid_current"],
                     reference_grid_action="HOLD", reference_grid_entry_source_target=s["grid_entry_source_current"],
                     reference_grid_transition_reason="fear_unavailable", reference_total_units_target=s["total_units_target"],
                     reference_total_units_change=0, grid_target=None, grid_action="UNCONFIRMED", grid_entry_source_target=None,
                     grid_transition_reason=None, total_units_target=None, total_units_change=None)
        d.update(status="partial", signal_completeness="partial",
                 incomplete_components=["IC.grid", "IC.total_futures", "IM.grid", "IM.total_futures"])
    alter_json(inputs["icim_result"], change)
    result = renderer.render(**inputs)
    for row in result["manifest"]["rows"][-2:]:
        assert row["target"] is None and row["action"] == "未确认（完整目标缺失）"
    assert "网格及合计目标未确认" in result["report"]


def test_live_microcap_manifest_keeps_actual_publication_external(inputs, monkeypatch):
    monkeypatch.setattr(renderer, "now_bj", lambda: datetime.fromisoformat("2026-11-05T14:49:00+08:00"))
    inputs["mode"] = "intraday"
    result = renderer.render(**inputs)
    assert result["manifest"]["needs_actual_publication"] is True
    assert result["manifest"]["actual_publication"] is False
    assert result["manifest"]["publication_deadline"] == "2026-11-05T15:00:00+08:00"
    assert result["manifest"]["publication_deadline_exclusive"] is True


def test_incomplete_microcap_group_never_publishes(inputs):
    del inputs["microcap_csv"]["v2.5"]
    with pytest.raises(ValueError, match="exactly"): renderer.render(**inputs)


def test_late_generation_after_render_still_rejects_microcap(inputs, monkeypatch):
    clocks = iter([datetime.fromisoformat("2026-11-05T14:59:59+08:00"), datetime.fromisoformat("2026-11-05T15:00:00+08:00")])
    monkeypatch.setattr(renderer, "now_bj", lambda: next(clocks))
    inputs["mode"] = "intraday"
    with pytest.raises(ValueError, match="crossed 15:00"): renderer.render(**inputs)
