import copy
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_ic_im_v1_4_digest as digest
import ic_im_v1_4_delivery_contract as contract
import prepare_ic_im_v1_4_marker as marker


def payload(monkeypatch, source="csv", missing=False):
    saved = json.loads((ROOT / "tests/fixtures/icim/readiness_20261009/result.json").read_text(encoding="utf-8"))
    clock = datetime(2026, 10, 16, 12, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(contract, "_validation_clock", lambda: clock)
    saved.update(signal_timing="retrospective_replay", report_generated_at=clock.isoformat())
    for s in saved["signals"].values():
        s.update(fear_source_policy_revision="baifenwei_either_source_20261016_v1",
                 signal_generated_at=clock.isoformat(), signal_generation_timing="retrospective_replay",
                 fear_fetch_started_at="2026-10-16T12:00:00+08:00", fear_retrieved_at="2026-10-16T12:01:00+08:00",
                 fear_greed_index=None if missing else 40., fear_data_date=None if missing else "2026-10-09",
                 fear_data_status="unavailable" if missing else "retrospective_replay", fear_fallback_applied=False,
                 fear_available_sources=[] if missing else [source], fear_selected_source=None if missing else source,
                 fear_cross_validation="unavailable" if missing else "single_source",
                 fear_source_url=None if missing else contract.CSV_URL if source == "csv" else contract.PAGE_URL,
                 fear_csv_sha256="a"*64 if source == "csv" and not missing else None,
                 fear_page_sha256="b"*64 if source == "page" and not missing else None,
                 fear_page_updated_at="2026-10-09T15:03:00+08:00" if source == "page" and not missing else None,
                 fear_page_data_date="2026-10-09" if source == "page" and not missing else None,
                 fear_latest_available_date=None if missing else "2026-10-09",
                 fear_source_warning="百分位没有合格读数" if missing else "仅取得单侧数据，未完成双侧验证",
                 fear_unavailable_reason="百分位没有合格读数" if missing else None)
        if missing:
            s.update(signal_completeness="partial", incomplete_components=["grid", "total_futures"],
                     reference_grid_target=s["grid_current"], reference_grid_action="HOLD",
                     reference_grid_entry_source_target=s["grid_entry_source_current"],
                     reference_grid_transition_reason="fear_unavailable", reference_total_units_target=s["total_units_target"],
                     reference_total_units_change=0, grid_target=None, grid_action="UNCONFIRMED",
                     grid_entry_source_target=None, grid_transition_reason=None, total_units_target=None, total_units_change=None)
    if missing:
        saved.update(status="partial", signal_completeness="partial", incomplete_components=["IC.grid", "IM.grid"])
    saved["jiu_reference"] = dict(jiu_fear_greed_index=2.28, jiu_data_date="2026-10-08",
        jiu_retrieved_at=clock.isoformat(), jiu_status="reference_only", jiu_reference_only=True)
    return saved


@pytest.mark.parametrize("source", ["csv", "page"])
def test_one_source_and_jiu_survive_mail_and_marker(monkeypatch, source):
    data = payload(monkeypatch, source)
    original = copy.deepcopy(data)
    subject, text, _ = digest.build_success(data, "", "")
    html = digest.build_success_html(data, "")
    assert "未完成双侧验证" in text and "未完成双侧验证" in html
    assert "9圈恐慌贪婪指数：2.28" in text and "数据日不同" in html
    assert "仅供参考，不参与网格信号" in digest.grid_reason("IC", {**data["signals"]["IC"], **data["jiu_reference"]})
    assert marker.marker_name(data)
    assert data == original


def test_no_fear_keeps_partial_mail_and_other_modules(monkeypatch):
    data = payload(monkeypatch, missing=True)
    subject, text, _ = digest.build_success(data, "", "")
    html = digest.build_success_html(data, "")
    assert "网格未确认" in subject and "其他模块照常展示" in text
    assert "Put" in text and "动量" in text and "N/A" in html
    assert marker.marker_name(data)
    assert data["signals"]["IC"]["grid_target"] is None


def test_partial_does_not_allow_hidden_grid_change(monkeypatch):
    data = payload(monkeypatch, missing=True)
    data["signals"]["IC"]["reference_grid_target"] = .5
    with pytest.raises(ValueError, match="preserve grid state"):
        digest.validate_success_payload(data)
