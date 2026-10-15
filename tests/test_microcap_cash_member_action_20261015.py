"""Real final-CSV regression plus synthetic transition/date boundary diagnostics.

The frozen row below is the 2026-10-15 v2.5 final CSV at strategy 35327072;
raw CSV SHA256 31edbc76c53116559f81925d22d8f13e8e5206450962e7054737edfd2e23589a;
canonical CSV SHA256 6c3e1daf2306445a8a2b8b470d2f768fdf2d1a67a50c2b626e5d34efa3315f00.
It is an acceptance fixture, never a current investment instruction.
"""
import csv
import json
import sys
from datetime import datetime
from unittest.mock import patch

import pytest

import test_microcap_digest_email_body as fixtures


digest = fixtures.digest
REAL_V25 = {
    "signal_label": "cash",
    "current_holding": "cash",
    "next_holding": "cash",
    "trade_state": "hold",
    "momentum_trade_state": "hold",
    "member_rebalance_state": "rebalance",
    "member_rebalance_required": "True",
    "member_enter_count": "11",
    "member_exit_count": "11",
    "member_rebalance_label": "名单调仓（调入 11，调出 11）",
    "microcap_mom": "-1.0105140757162818",
    "hedge_mom": "0.0",
    "momentum_gap": "-1.0105140757162818",
    "microcap_close": "211580.92621514463",
    "hedge_close": "6678.649",
    "effective_trade_state": "hold",
    "holding_trade_state": "hold",
    "scale_trade_state": "hold_scale",
    "scale_trade_required": "False",
    "position_transition": "False",
    "raw_scale_delta": "0.0",
    "actionable_scale_delta": "0.0",
    "scale_delta": "0.0",
    "position_scale_delta": "0.0",
    "current_execution_scale": "0.0",
    "target_position_scale": "0.0",
    "next_session_target_scale": "0.0",
    "raw_next_target_scale": "0.0",
    "next_session_actionable_scale": "0.0",
    "target_vol_scale_next_session": "0.0",
    "next_session_turnover": "0.0",
    "next_session_leg_turnover": "0.0",
    "next_session_leg_cost_est_raw": "0.0",
    "next_session_overlay_cost_est": "0.0",
    "next_session_trade_cost_est": "0.0",
    "next_session_overlay_trade_cost_est": "0.0",
    "next_session_trade_cost_est_type": "overlay_only",
    "next_session_total_trade_cost_est_note": "estimated from v2.5 unhedged exposure turnover and the configured one-side entry/exit cost",
    "target_vol_signal_timing": "",
    "signal_timing": "close_confirmed",
    "official_close_confirmed_signal": "True",
    "target_vol_realtime_snapshot_vol_frozen": "False",
    "target_vol_frozen_lag_days": "0",
    "target_vol_frozen_lag_calendar_days": "0",
    "target_vol_frozen_lag_trading_days": "0",
    "target_vol_frozen_source_date": "2026-10-15",
    "return_column_semantics": "return equals return_net after selected v2.5 microcap-only log-WLS threshold and base costs; use base_pre_cost_return for pre-cost return",
    "execution_scale": "0.0",
    "target_vol_realized_vol": "0.0",
    "target_vol_turnover": "0.0",
    "target_vol_costed_turnover": "0.0",
    "scale_change_cost": "0.0",
    "target_vol_trade_cost": "0.0",
    "financing_cost": "0.0",
    "idle_cash_yield": "0.0",
    "return_gross_target_vol": "0.0",
    "signal_quality_derisk_triggered": "False",
    "overheat_enabled": "False",
    "overheat_kind": "disabled",
    "overheat_window": "0",
    "overheat_threshold": "0.0",
    "overheat_metric_name": "disabled",
    "overheat_triggered": "False",
    "blocked_until_signal_reset": "False",
    "signal_reset_seen": "False",
    "overheat_require_positive_trade_return": "False",
    "overheat_require_signal_reset": "False",
    "fixed_hedge_ratio": "0.0",
    "momentum_gap_exit_buffer": "0.0",
    "signal_quality_derisk_enabled": "False",
    "version": "2.5",
    "base_version": "embedded_v2_base",
    "overlay_type": "plain_lb20_hl3_entry0_exit0_20260905",
    "strategy_revision": "plain_lb20_hl3_entry0_exit0_20260905",
    "lookback": "20",
    "target_vol_enabled": "False",
    "target_vol": "0.0",
    "target_vol_window": "0",
    "max_leverage": "1.0",
    "date": "2026-10-15",
    "strategy_version": "v2.5",
    "signal_model": "microcap_only_log_wls_exp_halflife_3p0_lb20_entry0_exit0_no_targetvol",
    "signal_spread_hedge_ratio": "0.0",
    "execution_hedge_ratio": "0.0",
    "hedge_removed": "True",
    "halflife": "3.0",
    "entry_threshold": "0.0",
    "exit_threshold": "0.0",
    "single_trade_stop_loss_enabled": "False",
    "equity_drawdown_overlay_enabled": "False",
    "momentum_decay_overlay_enabled": "False",
    "overheat_overlay_enabled": "False",
    "signal_score_label": "microcap_only_annualized_log_wls_score",
    "schema_version": "log_wls_score_schema_v1",
    "momentum_gap_deprecated": "True",
    "gap_peak": "",
    "gap_decay_ratio": "",
    "overheat_metric": "",
    "trade_return_net": "",
    "latest_realized_vol": "",
    "return_gross_base": "",
    "momentum_gap_legacy_note": "legacy field contains annualized microcap-only log-WLS score, not plain microcap-minus-hedge momentum gap",
    "annualized_log_wls_score": "-1.0105140757162818",
    "log_wls_r2": "0.22219543188965019",
    "microcap_nav": "215.940887112395",
    "target_vol_scale_rebalance_threshold": "0.0",
    "target_vol_max_leverage": "1.0",
    "cash_day_yield": "0.0",
    "cash_day_yield_annual": "0.0",
    "cash_day_yield_enabled": "False",
    "financing_enabled": "False",
    "member_rebalance_signal_date": "2026-10-15",
    "member_rebalance_execution_date": "2026-10-16",
    "member_rebalance_actionable": "True",
    "member_rebalance_official": "True",
    "microcap_series_source": "local_cache_proxy_recent_extension",
    "official_wind_series": "False",
    "proxy_warning": "When source_used is public/local proxy, the microcap series is reconstructed from local/public A-share price, share-change, membership, and tradeability data. It is not the official Wind 868008.WI series."
}


def render(tmp_path, row, version="v2.5", *, mode="close_confirmed", stdout=""):
    source = tmp_path / "final.csv"
    with source.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    output = tmp_path / "result.txt"
    output.write_text(("signal" if mode == "close_confirmed" else "realtime_signal") + "\n" + stdout, encoding="utf-8")
    argv = ["digest", "--result", f"{version}={output}", "--signal-csv", f"{version}={source}",
            "--exit-code", f"{version}=0", "--out-dir", str(tmp_path / "rendered"), "--publication-mode", mode]
    if mode == "close_confirmed":
        argv += ["--expected-signal-date", "2026-10-15"]
    before = source.read_bytes()
    with patch.object(sys, "argv", argv), patch.object(digest, "now_bj", return_value=datetime(2026, 10, 16, 10, tzinfo=digest.BJ)):
        assert digest.main() == 0
    assert source.read_bytes() == before
    return json.loads((tmp_path / "rendered/metadata.json").read_text(encoding="utf-8"))


def row_for(version, current, nxt):
    # Derived cases are synthetic controls; REAL_V25 itself remains unmodified.
    row = fixtures.MicrocapDigestEmailBodyTests().signal_fields(version, current, nxt)
    row.update({key: value for key, value in REAL_V25.items() if key.startswith("member_")})
    action = "hold" if current == nxt else "open" if current == "cash" else "close"
    row.update(date="2026-10-15", signal_timing="close_confirmed", official_close_confirmed_signal="True",
               trade_state=action, holding_trade_state=action)
    return row


def test_real_v25_cash_member_update_is_background_not_trade(tmp_path):
    result = render(tmp_path, REAL_V25)
    assert result["status"] == "OK"
    assert "[下个交易日无需操作]" in result["subject"]
    assert "| v2.5 | 空仓 → 空仓 | 无操作 | 0.00 |" in result["body"]
    assert "v2.5 需要名单调仓" not in result["body"]
    assert "名单更新背景" in result["body"]
    assert "调入 11，调出 11；信号日 2026-10-15，执行日 2026-10-16" in result["body"]
    assert "当前及下一交易日均为空仓，无需买卖" in result["body"]


@pytest.mark.parametrize("version,active", [("v2.0", "long_microcap_short_zz1000"),
                                           ("v2.3", "long_microcap_short_zz1000"),
                                           ("v2.5", "long_microcap_top100")])
def test_active_member_rotation_remains_actionable(tmp_path, version, active):
    result = render(tmp_path, row_for(version, active, active), version)
    assert result["status"] == "OK"
    assert f"{version} 需要名单调仓" in result["body"]
    assert "执行日 2026-10-16" in result["body"]
    assert "名单更新背景" not in result["body"]


@pytest.mark.parametrize("current,nxt,action", [("cash", "long_microcap_short_zz1000", "开仓"),
                                               ("long_microcap_short_zz1000", "cash", "平仓")])
def test_entry_and_exit_are_not_suppressed_by_member_event(tmp_path, current, nxt, action):
    result = render(tmp_path, row_for("v2.3", current, nxt), "v2.3")
    assert result["status"] == "OK"
    assert f"v2.3 需要{action}" in result["body"]
    assert "名单更新背景" not in result["body"]


@pytest.mark.parametrize("changes", [
    {"next_session_actionable_scale": "1"},
    {"current_execution_scale": "nan"},
    {"strategy_revision": "retired"},
    {"version": "2.3"},
    {"member_rebalance_signal_date": "2026-10-14", "member_rebalance_execution_date": "2026-10-15"},
    {"member_rebalance_execution_date": "2026-10-14"},
])
def test_invalid_identity_scale_or_dated_event_is_still_rejected(tmp_path, changes):
    result = render(tmp_path, {**REAL_V25, **changes})
    assert result["status"] == "FAILED"
    assert "| v2.5 | 不可用 → 不可用 | 异常 |" in result["body"]
    assert "名单更新背景" not in result["body"]


def test_zero_target_scale_cannot_hide_invalid_active_holding(tmp_path):
    row = row_for("v2.3", "long_microcap_short_zz1000", "long_microcap_short_zz1000")
    row["next_session_actionable_scale"] = "0"
    assert render(tmp_path, row, "v2.3")["status"] == "FAILED"


def test_stdout_cannot_promote_real_cash_csv_to_active_member_trade(tmp_path):
    stdout = "current_holding: long_microcap_top100\nnext_holding: long_microcap_top100\nnext_session_actionable_scale: 1\n"
    result = render(tmp_path, REAL_V25, stdout=stdout)
    assert result["status"] == "OK"
    assert "| v2.5 | 空仓 → 空仓 | 无操作 | 0.00 |" in result["body"]


def test_realtime_cash_event_on_execution_day_remains_background(tmp_path):
    row = {**REAL_V25, "date": "2026-10-16", "quote_trade_date": "2026-10-16",
           "latest_anchor_trade_date": "2026-10-15", "expected_latest_completed_trade_date": "2026-10-15",
           "snapshot_time": "2026-10-16T09:55:00+08:00", "signal_timing": "intraday_hypothetical_if_now_close",
           "official_close_confirmed_signal": "False"}
    result = render(tmp_path, row, mode="realtime")
    assert result["status"] == "OK" and "[无需操作]" in result["subject"]
    assert "| v2.5 | 空仓 → 空仓 | 无操作 | 0.00 |" in result["body"]
    assert "信号日 2026-10-15，执行日 2026-10-16" in result["body"]
