"""Protect first sessions after holidays and the strict intraday deadline."""
from datetime import datetime
from unittest.mock import patch

import pytest

import test_microcap_digest_email_body as fixtures


digest = fixtures.digest
NEXT_OPEN = datetime(2026, 10, 8, 14, 15, tzinfo=digest.BJ)
VERSIONS = ("v2.0", "v2.3", "v2.5")


def complete_row(version):
    return {
        **fixtures.MicrocapDigestEmailBodyTests().identity_fields(version),
        "date": "2026-10-08",
        "quote_trade_date": "2026-10-08",
        "latest_anchor_trade_date": "2026-09-30",
        "expected_latest_completed_trade_date": "2026-09-30",
        "expected_latest_completed_trade_date_source": "independent_close_history_refresh",
        "snapshot_time": "2026-10-08T14:15:00+08:00",
        "official_close_confirmed_signal": "False",
        "signal_timing": "intraday_hypothetical_if_now_close",
        "current_holding": "cash",
        "next_holding": "cash",
        "current_execution_scale": "0",
        "next_session_actionable_scale": "0",
    }


def run_digest(tmp_path, version, row, now=NEXT_OPEN):
    result = tmp_path / "result.txt"
    result.write_text(
        "realtime_signal\nquote_trade_date: 2026-10-08\n"
        "latest_anchor_trade_date: 2026-09-30\n", encoding="utf-8")
    csv_path = tmp_path / "signal.csv"
    fixtures.MicrocapDigestEmailBodyTests().write_csv(csv_path, row)
    argv = ["build_microcap_realtime_digest.py", "--result", f"{version}={result}",
            "--signal-csv", f"{version}={csv_path}", "--exit-code", f"{version}=0",
            "--out-dir", str(tmp_path / "artifacts"), "--publication-mode", "realtime"]
    import json
    import sys
    with patch.object(digest, "now_bj", return_value=now), patch.object(sys, "argv", argv):
        assert digest.main() == 0
    return json.loads((tmp_path / "artifacts/metadata.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("version", VERSIONS)
def test_first_session_after_national_day_uses_verified_csv_anchor(tmp_path, version):
    # 10/01 through 10/07 are closed. 09/30 is the completed exchange session,
    # even though the previous weekday is 10/07.
    meta = run_digest(tmp_path, version, complete_row(version))
    assert meta["status"] == "OK"
    assert "数据过期" not in meta["body"]


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("changes", [
    {"latest_anchor_trade_date": "2026-09-29"},
    {"latest_anchor_trade_date": ""},
    {"expected_latest_completed_trade_date": ""},
    {"latest_anchor_trade_date": "2026-10-08", "expected_latest_completed_trade_date": "2026-10-08"},
    {"quote_trade_date": "2026-09-30", "date": "2026-09-30"},
    {"snapshot_time": "2026-10-08T14:15:00"},
    {"snapshot_time": "2026-09-30T14:15:00+08:00"},
    {"signal_timing": "close_confirmed", "official_close_confirmed_signal": "True"},
])
def test_fresh_stdout_never_repairs_invalid_final_csv(tmp_path, version, changes):
    meta = run_digest(tmp_path, version, {**complete_row(version), **changes})
    assert meta["status"] == "FAILED"
    assert "今日操作" in meta["body"]
    assert "不可用" in meta["body"]


@pytest.mark.parametrize("snapshot", [
    "2026-10-08T14:59:59+08:00",
    "2026-10-08T06:59:59+00:00",
])
def test_utc_equivalent_snapshot_before_deadline_is_valid(snapshot):
    row = {**complete_row("v2.0"), "snapshot_time": snapshot}
    with patch.object(digest, "now_bj", return_value=datetime(2026,10,8,14,59,59,tzinfo=digest.BJ)):
        assert digest.validate_publication_contract("realtime", row) == (True, "")


@pytest.mark.parametrize("snapshot", [
    "2026-10-08T15:00:00+08:00",
    "2026-10-08T15:29:59+08:00",
    "2026-10-08T07:00:00+00:00",
])
def test_snapshot_at_or_after_close_cannot_be_realtime(snapshot):
    row = {**complete_row("v2.0"), "snapshot_time": snapshot}
    with patch.object(digest, "now_bj", return_value=NEXT_OPEN):
        valid, note = digest.validate_publication_contract("realtime", row)
    assert not valid
    assert "before 15:00 Asia/Shanghai" in note


@pytest.mark.parametrize("now", [
    datetime(2026,10,8,15,0,tzinfo=digest.BJ),
    datetime(2026,10,8,15,29,59,tzinfo=digest.BJ),
    datetime.fromisoformat("2026-10-08T07:00:00+00:00"),
])
def test_delayed_publication_cannot_send_earlier_snapshot_as_realtime(now):
    with patch.object(digest, "now_bj", return_value=now):
        valid, note = digest.validate_publication_contract("realtime", complete_row("v2.0"))
    assert not valid
    assert "before 15:00 Asia/Shanghai" in note


def test_close_confirmed_publication_stays_available_after_close():
    row = {**complete_row("v2.0"), "date": "2026-09-30", "signal_timing": "close_confirmed",
           "official_close_confirmed_signal": "True"}
    with patch.object(digest, "now_bj", return_value=datetime(2026,10,7,22,30,tzinfo=digest.BJ)):
        assert digest.validate_publication_contract("close_confirmed", row) == (True, "")
