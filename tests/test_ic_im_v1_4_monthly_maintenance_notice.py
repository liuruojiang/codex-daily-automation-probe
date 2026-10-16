"""Display regressions: reminders must not be hidden by HOLD or imply a fill."""
import copy
from datetime import date
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_ic_im_v1_4_digest as digest
import check_ic_im_v1_4_delivery as gate
import ic_im_option_maintenance_notice as notice


def payload(intraday=False):
    # Synthetic pre-strict-identity display fixture; it still passes the real
    # success/marker gates, without monkeypatching the validator or ledger.
    day = "2026-09-18" if intraday else "2026-09-17"
    next_day = "2026-09-21" if intraday else "2026-09-18"
    signals = {}
    for product in ("IC", "IM"):
        signals[product] = {
            "product": product, "market_date": day, "next_trade_date": next_day,
            "close_confirmed": not intraday,
            "core_action": "HOLD", "momentum_action": "HOLD", "grid_action": "HOLD",
            "put_action": "HOLD", "call_action": "HOLD", "v14_action": "HOLD",
            "total_units_current": 0.5, "total_units_target": 0.5,
            "momentum_current_weight": 0.0, "momentum_next_weight": 0.0,
            "grid_current": 0.0, "grid_target": 0.0,
            "option_monthly_reset_preview": not intraday,
            "put_monthly_reset_preview": not intraday,
            "option_monthly_reset_execution_date": "2026-09-18",
            "put_monthly_reset_execution_date": "2026-09-18",
        }
    return {
        "status": "ok", "strategy_revision": "r1",
        "build": gate.LEGACY_BUILD, "delivery_revision": gate.LEGACY_DELIVERY_REVISION,
        "publication_mode": "realtime" if intraday else "close_confirmed",
        "market_date": day, "completed_day": "2026-09-17",
        "verified_day": "2026-09-17", "next_trade_day": next_day,
        "sequence": 1, "digest": "a" * 64, "signals": signals,
    }


@pytest.mark.parametrize("intraday", [False, True])
def test_reminder_visible_in_subject_plain_and_html_with_all_hold(intraday):
    item = payload(intraday)
    before = copy.deepcopy(item)
    subject, text, actionable = digest.build_success(item, "", "")
    html = digest.build_success_html(item, "")
    assert "[月度Put需维护]" in subject and "无需调整" not in subject
    assert actionable is False  # independent maintenance notice, no new target/fill
    assert "IC、IM均无需调整" not in text
    assert "盘中暂时无需调整" not in html
    for rendered in (text, html):
        assert rendered.count("月度Put展期换仓提醒") == 2
        assert ("今日（2026-09-18）" if intraday else "明日（2026-09-18）") in rendered
        assert "盘尾" in rendered and "自行计算判断" in rendered
        assert "2026-09-18 当天收盘完成换仓" in rendered
        assert "不提前锁定新合约" in rendered
    assert html.index("月度Put展期换仓提醒") < html.index("今日恐慌指数")
    assert text.index("月度Put展期换仓提醒") < text.index("| 品种 |")
    assert item == before


def test_no_notice_on_actual_maintenance_day_after_close():
    item = payload(True)
    for signal in item["signals"].values():
        signal["close_confirmed"] = True
    assert not notice.reminders(item["signals"])


def test_stale_and_missing_calendar_not_used_and_untrusted_top_level_ignored():
    item = payload()
    item["option_maintenance_notices"] = ["UNTRUSTED-CONTRACT-ORDER"]
    for signal in item["signals"].values():
        signal["next_trade_date"] = "2026-09-21"
    assert not notice.reminders(item["signals"])
    assert "UNTRUSTED-CONTRACT-ORDER" not in digest.build_success_html(item, "")


def test_historical_reminder_does_not_claim_original_publication():
    item = payload()
    item["signal_timing"] = "retrospective_replay"
    for signal in item["signals"].values():
        signal["signal_generation_timing"] = "retrospective_replay"
    subject, text, _ = digest.build_success(item, "", "")
    html = digest.build_success_html(item, "")
    assert "历史模型补录" in subject and "月度维护日历复核" in subject
    assert "非原日已发布提醒" in text and "非原日已发布提醒" in html
    assert "明日（" not in text


def test_dates_as_objects_and_no_calendar_weekday_guess():
    item = payload()["signals"]["IC"]
    item.update(market_date=date(2026, 6, 18), next_trade_date=date(2026, 6, 22),
                option_monthly_reset_execution_date=date(2026, 6, 22))
    assert "下一交易日（2026-06-22）" in notice.reminder("IC", item, is_session=digest.delivery_contract.is_session)
    assert not notice.reminder("IC", item)


def test_partial_calendar_does_not_suppress_warning_or_expose_stale_targets():
    item = payload(True)
    item["status"] = "partial"
    item["signals"]["IC"]["put_target_contract"] = "DO-NOT-PUBLISH-THIS-TARGET"
    subject, text = digest.build_failure(item, "", "")
    html = digest.build_failure_html(item, "")
    assert "[警告]" in subject and "月度Put维护日历" in subject
    for rendered in (text, html):
        assert "尚未确认" in rendered
        assert rendered.count("月度Put展期换仓提醒") == 2
        assert "DO-NOT-PUBLISH-THIS-TARGET" not in rendered
    item["signals"]["IC"]["market_date"] = "2026-09-17"
    assert not digest.partial_calendar_notices(item)
    item["status"] = "failed"
    assert not digest.partial_calendar_notices(item)
