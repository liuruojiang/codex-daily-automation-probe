"""Presentation boundary cases; synthetic envelope, no market or send calls."""
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ic_im_intraday_observations as obs
import build_ic_im_v1_4_digest as digest
import build_local_intraday_report as local


@pytest.fixture
def payload():
    # Deliberately synthetic software case; amounts exercise publication shape.
    signal = dict(product="IM", market_date="2026-11-06", close_confirmed=False,
        market_phase="盘中", state_anchor_day="2026-11-05", v14_build_id="fixture-build",
        next_trade_date="2026-11-09",future_quote_date="2026-11-06",core_current="IM2612",
        future_quote_source="synthetic",future_last=8000.,signal_generated_at="2026-11-06T14:30:40+08:00",
        future_quote_evidence=dict(contract="IM2612",source="synthetic",source_date="2026-11-06",
            row_source_date="2026-11-06",lastprice=8000.,collected_at="2026-11-06T14:30:30+08:00",
            source_snapshot_at="2026-11-06T14:30:00+08:00",
            source_snapshot_kind="latest_available_contract_update"),
        calendar_partial_revision=obs.CALENDAR_PARTIAL_REVISION, calendar_provisional=True,
        signal_complete=False, persistence_eligible=False, futures_target_confirmed=True,
        unconfirmed_put_legs=["momentum"], momentum_put_target_contract=None,
        momentum_put_target_qty_normalized=None, total_put_target_qty_normalized=None,
        momentum_put_required_qty_normalized=.75, momentum_120=-.2,
        momentum_current_weight=0., momentum_next_weight=.5,
        core_units_current=.5, core_units_target=.5, momentum_units_current=0.,
        momentum_units_target=.25, grid_current=.5, grid_target=.5,
        total_units_current=1., total_units_target=1.25,
        core_put_current_contract="MO2612-P-6700", total_put_current_qty_normalized=1.5)
    return dict(status="partial", publication_contract_revision=obs.REVISION,
        publication_mode="realtime", market_date="2026-11-06", completed_day="2026-11-05",
        verified_day="2026-11-05", next_trade_day="2026-11-09",
        generated_at="2026-11-06T14:30:00+08:00", report_generated_at="2026-11-06T14:31:00+08:00",
        ledger_unchanged=True, ledger_recovery_eligible=False, persistence_eligible=False,
        production_authorized=False, complete_portfolio_target=False, actual_publication=False,
        build="fixture-build", source_report="IC取得报价\nIM待核验目标999\n<script>unsafe()</script>",
        product_observations={"IC":dict(status="unverified", validation_error="missing quote",
            signal=dict(product="IC", total_units_target=999)),
            "IM":dict(status="partial", validation_error=None, signal=signal)})


def test_partial_text_html_and_local_summary_keep_momentum_requirement(payload):
    before = copy.deepcopy(payload)
    digest.validate_success_payload(payload)
    subject, body, actionable = digest.build_success(payload, "", "")
    assert "盘中信息" in subject and "0→0.25" in body
    assert "需要0.75张" in body and "保护未落实" in body
    assert "Put合约未确认不代表无需保护" in body and actionable
    assert "|IC|unverified|N/A→N/A|N/A→N/A|" in body
    assert "> IM待核验目标999" in body
    html = digest.build_success_html(payload, "")
    assert "&lt;script&gt;" in html and "<script>" not in html
    rows, report = local._icim_rows(payload, "2026-11-06", "2026-11-05", "2026-11-09")
    assert rows[0]["target"] is None and rows[1]["momentum_target"] == .25
    assert "0.75" in report and payload == before


def test_old_ledger_date_is_explicit_not_falsely_advanced(payload):
    payload["verified_day"] = "2026-11-04"
    payload["product_observations"]["IM"].update(status="unverified", validation_error="账本锚点较旧")
    _, body, _ = digest.build_success(payload, "", "")
    assert "实际账本锚点：2026-11-04" in body
    assert local._icim_rows(payload, "2026-11-06", "2026-11-05", "2026-11-09")


def test_stale_ledger_cannot_qualify_a_target(payload):
    payload["verified_day"] = "2026-11-04"
    with pytest.raises(ValueError): obs.validate(payload)


@pytest.mark.parametrize("flag", ["ledger_recovery_eligible", "persistence_eligible",
    "production_authorized", "complete_portfolio_target", "actual_publication"])
def test_information_cannot_promote_execution_or_ledger_qualification(payload, flag):
    payload[flag] = True
    with pytest.raises(ValueError): digest.build_success(payload, "", "")


@pytest.mark.parametrize("key,value", [
    ("momentum_put_required_qty_normalized", 0),
    ("momentum_put_target_contract", "MO2701-P-6700"),
    ("momentum_put_target_qty_normalized", 0),
    ("total_put_target_qty_normalized", 1.5),
    ("momentum_units_target", .5),
    ("total_units_target", 1.),
    ("futures_target_confirmed", False),
    ("diagnostic_only", True),
])
def test_partial_cannot_hide_missing_put_or_forge_confirmed_targets(payload, key, value):
    payload["product_observations"]["IM"]["signal"][key] = value
    with pytest.raises(ValueError): obs.validate(payload)


def test_calendar_partial_cannot_be_relabelled_verified(payload):
    payload["product_observations"]["IM"]["status"] = "verified"
    with pytest.raises(ValueError): obs.validate(payload)


def test_no_available_product_still_reports_missing_information(payload):
    payload["product_observations"] = {p:dict(status="unavailable",signal=None,validation_error="未取得") for p in ("IC","IM")}
    _, body, actionable = digest.build_success(payload,"","")
    assert body.count("|unavailable|N/A→N/A|") == 2 and not actionable


def test_unknown_information_revision_stays_on_original_signal_contract(payload):
    # An unrecognised envelope cannot bypass the existing full-signal contract.
    payload["publication_contract_revision"] = "unreviewed-information-revision"
    with pytest.raises((ValueError,KeyError)):
        digest.build_success(payload,"","")


def test_saved_diagnostic_rerender_is_not_a_new_intraday_publication(payload):
    payload.update(saved_diagnostic_rerender=True, signal_timing="saved_intraday_diagnostic_rerender",
                   report_generated_at="2026-11-06T15:20:00+08:00")
    subject, body, _ = digest.build_success(payload, "", "")
    assert "补充整理" in subject and "不具有新的盘中发布资格" in body
    assert "IC=unverified" in body and "IM=partial" in body
    assert "不具有新的盘中发布资格" in digest.build_success_html(payload, "")


def test_saved_rerender_cannot_acquire_intraday_local_publication_eligibility(payload,tmp_path):
    payload.update(saved_diagnostic_rerender=True,signal_timing="saved_intraday_diagnostic_rerender")
    path=tmp_path/'saved.json'
    path.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
    with pytest.raises(ValueError,match="requires replay mode"):
        local.render(mode='intraday',signal_day='2026-11-06',anchor_day='2026-11-05',
                     next_session='2026-11-09',icim_result=path)
    replay=local.render(mode='replay',signal_day='2026-11-06',anchor_day='2026-11-05',
                        next_session='2026-11-09',icim_result=path)
    assert replay['manifest']['mode']=='replay' and replay['manifest']['needs_actual_publication'] is False
    assert '不是新的盘中信号' in replay['summary']


def test_extreme_numeric_observation_is_unavailable_not_an_overflow():
    assert obs.number(10 ** 1000) == "N/A"


def test_malformed_optional_iv_observation_does_not_hide_other_information(payload):
    payload["product_observations"]["IC"]["signal"]["iv_warning"] = "unstructured provider text"
    _, body, _ = digest.build_success(payload,"","")
    assert "IV：N/A（本次未核验）" in body and "0→0.25" in body


def test_unknown_next_session_with_warning_still_reports_available_information(payload):
    payload.update(next_trade_day=None, calendar_warning="下一交易日缺少正式日历覆盖")
    payload["product_observations"]["IM"].update(status="unverified", validation_error=payload["calendar_warning"])
    _, body, actionable = digest.build_success(payload, "", "")
    assert "N/A（正式日历未确认）" in body and "下一交易日缺少正式日历覆盖" in body
    assert "IC取得报价" in body and "|IM|unverified|N/A→N/A|" in body
    assert not actionable
    assert "N/A（正式日历未确认）" in digest.build_success_html(payload, "")


def test_local_renderer_retains_unknown_next_session_observations(payload, tmp_path):
    payload.update(next_trade_day=None, calendar_warning="下一日尚未确认")
    payload["product_observations"]["IM"].update(status="unverified", validation_error="下一日尚未确认")
    path = tmp_path / "observations.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    out = local.render(mode="replay", signal_day="2026-11-06", anchor_day="2026-11-05",
                       next_session=None, icim_result=path)
    assert "N/A（正式日历未确认）" in out["report"] and "IC取得报价" in out["report"]


@pytest.mark.parametrize("invalid", ["qualified_target", "missing_warning"])
def test_unknown_next_session_cannot_confirm_targets_or_omit_warning(payload, invalid):
    payload.update(next_trade_day=None, calendar_warning="下一交易日缺少正式日历覆盖")
    if invalid == "missing_warning":
        payload["product_observations"]["IM"].update(status="unverified", validation_error="待核验")
        payload.pop("calendar_warning")
    with pytest.raises(ValueError): digest.build_success(payload, "", "")
