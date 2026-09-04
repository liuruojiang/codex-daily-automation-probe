"""Isolated CSV fault injection; no SMTP, market queries, or performance claims."""
import pytest

import test_microcap_digest_email_body as fixtures


def complete_row(version):
    row = fixtures.MicrocapDigestEmailBodyTests().identity_fields(version)
    return {**row, "date": "2026-09-03", "signal_timing": "close_confirmed",
            "official_close_confirmed_signal": "True", "current_holding": "cash",
            "next_holding": "cash", "current_execution_scale": "0",
            "next_session_actionable_scale": "0", "trade_state": "hold", "holding_trade_state": "hold"}


def run(tmp_path, version, row, stdout_extra=""):
    # Deliberately do NOT repair row here: malformed/missing fields are the attack.
    return fixtures.MicrocapDigestEmailBodyTests().run_digest(
        tmp_path, {version: "signal\n" + stdout_extra}, {version: row},
        publication_mode="close_confirmed", expected_signal_date="2026-09-03")


@pytest.mark.parametrize("version", ["v2.0", "v2.3"])
def test_control_complete_final_csv_is_valid(tmp_path, version):
    assert run(tmp_path, version, complete_row(version))["status"] == "OK"


@pytest.mark.parametrize("version", ["v2.0", "v2.3"])
def test_final_holdings_cannot_be_backfilled_from_stdout(tmp_path, version):
    row = complete_row(version)
    row.pop("current_holding")
    row.pop("next_holding")
    result = run(tmp_path, version, row,
                 "current_holding: cash\nnext_holding: long_microcap_short_zz1000\n")
    assert result["status"] == "FAILED"
    assert "需要开仓" not in result["body"]


@pytest.mark.parametrize("version", ["v2.0", "v2.3"])
@pytest.mark.parametrize("value", ["inf", "-inf", "nan", "", "-1", "0.5"])
def test_invalid_actionable_scale_is_not_a_trade_instruction(tmp_path, version, value):
    row = {**complete_row(version), "next_session_actionable_scale": value}
    assert run(tmp_path, version, row)["status"] == "FAILED"


def test_historical_member_action_cannot_be_republished_today(tmp_path):
    row = {**complete_row("v2.3"), "member_rebalance_required": "True",
           "member_rebalance_actionable": "True", "member_rebalance_official": "True",
           "member_rebalance_signal_date": "2026-08-20", "member_rebalance_execution_date": "2026-08-21"}
    result = run(tmp_path, "v2.3", row)
    assert result["status"] == "FAILED"
    assert "需要名单调仓" not in result["body"]


def test_stdout_action_cannot_override_complete_cash_csv(tmp_path):
    result = run(tmp_path, "v2.3", complete_row("v2.3"),
                 "current_holding: cash\nnext_holding: long_microcap_short_zz1000\n"
                 "holding_trade_state: enter\nscale_trade_required: True\n"
                 "scale_trade_state: rebalance_scale\n")
    assert result["status"] == "OK"
    assert "需要开仓" not in result["body"]
    assert "调整仓位" not in result["body"]


def test_unsupported_holding_fails_closed_instead_of_rendering_injected_text(tmp_path):
    row = {**complete_row("v2.0"), "current_holding": "rogue\n## INJECT-HOLDING\n**break-holding**"}
    result = run(tmp_path, "v2.0", row)
    assert result["status"] == "FAILED"
    assert "## INJECT-HOLDING" not in result["body"]
    assert "**break-holding**" not in result["body"]


def realtime_run(tmp_path, row):
    # Fresh stdout must never repair stale or missing time fields in final CSV.
    return fixtures.MicrocapDigestEmailBodyTests().run_digest(tmp_path, {"v2.3":
        "realtime_signal\nquote_trade_date: 2026-08-07\nlatest_anchor_trade_date: 2026-08-06\n"
        "snapshot_time: 2026-08-07 09:33:00+08:00\n"}, {"v2.3": row})


def test_current_realtime_csv_control(tmp_path):
    row = fixtures.MicrocapDigestEmailBodyTests().signal_fields("v2.3")
    assert realtime_run(tmp_path, row)["status"] == "OK"


@pytest.mark.parametrize("mutation", [
    {"date": "2026-08-06", "quote_trade_date": "2026-08-06"},
    {"date": "2026-08-06"},
    {"snapshot_time": "2026-08-06 09:33:00+08:00"},
    {"snapshot_time": "2026-08-07 09:33\n## INJECT-SNAPSHOT"},
    {"latest_anchor_trade_date": "2026-08-07"},
    {"expected_latest_completed_trade_date": "2026-08-05"},
    {"official_close_confirmed_signal": "True"},
    {"signal_timing": "close_confirmed"},
    {"quote_trade_date": ""},
])
def test_fresh_stdout_does_not_authorize_invalid_realtime_csv(tmp_path, mutation):
    row = {**fixtures.MicrocapDigestEmailBodyTests().signal_fields("v2.3"), **mutation}
    assert realtime_run(tmp_path, row)["status"] == "FAILED"


@pytest.mark.parametrize("alias", ["trade_state", "holding_trade_state", "effective_trade_state"])
def test_conflicting_csv_action_alias_cannot_invent_trade(tmp_path, alias):
    result = run(tmp_path, "v2.3", {**complete_row("v2.3"), alias: "enter"})
    assert result["status"] == "OK"
    assert "需要开仓" not in result["body"]
    assert "所有版本均无需调仓" in result["body"]
