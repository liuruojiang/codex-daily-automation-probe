"""Adversarial publication boundaries, using the observed 2026-09-30 identities."""
from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_ic_im_v1_4_digest as digest
import check_ic_im_v1_4_delivery as gate


def payload():
    result = {
        "status": "ok", "strategy_revision": "r1", "build": gate.FIX9_BUILD,
        "signal_build": gate.FIX9_BUILD,
        "signal_rule_revision": "ic_im_v1_4_ordinaryput_open_ic_seller_mom120_20260929_v1",
        "delivery_revision": gate.FIX9_DELIVERY_REVISION,
        "publication_mode": "close_confirmed", "market_date": "2026-09-30",
        "completed_day": "2026-09-30", "verified_day": "2026-09-30",
        "next_trade_day": "2026-10-08", "sequence": 9,
        "digest": "f279667c5fd2cb39c0410ab62a5d026bdde69582f6968a417c17d3d47621f450",
        "signals": {},
    }
    for product in ("IC", "IM"):
        result["signals"][product] = {
            "product": product, "market_date": "2026-09-30", "next_trade_date": "2026-10-08",
            "close_confirmed": True, "market_phase": "收盘后",
            "strategy_version": "1.4", "strategy_revision": "r1",
            "v14_build_id": result["build"], "v14_rule_revision": result["signal_rule_revision"],
            "total_units_current": .5, "total_units_target": .5,
            "momentum_current_weight": 0., "momentum_next_weight": 0.,
            "grid_current": 0., "grid_target": 0., "call_target_qty_normalized": 0.,
            "call_action": "HOLD", "call_current_contract": None, "call_has_position": False,
        }
    return result


def test_observed_holiday_close_identity_remains_send_preparable():
    digest.validate_success_payload(payload())


@pytest.mark.parametrize("field,value", [
    ("v14_build_id", "wrong-version"),
    ("v14_rule_revision", "wrong-rule"),
    ("next_trade_date", "2026-10-09"),
    ("close_confirmed", False),
    ("product", "IC"),
    ("strategy_revision", "r0"),
    ("strategy_version", "1.3"),
    ("market_date", "2026-09-29"),
    ("market_phase", "盘中"),
    ("close_confirmed", 1),
    ("close_confirmed", "True"),
    ("close_confirmed", "False"),
])
def test_mixed_or_unconfirmed_leg_cannot_pass_send_preparation(field, value):
    result = deepcopy(payload())
    result["signals"]["IM"][field] = value
    with pytest.raises(ValueError):
        digest.validate_success_payload(result)


def test_rule_changed_in_top_level_and_both_legs_is_still_rejected():
    result = payload()
    result["signal_rule_revision"] = "wrong-rule"
    for signal in result["signals"].values():
        signal["v14_rule_revision"] = "wrong-rule"
    with pytest.raises(ValueError, match="authoritative"):
        digest.validate_success_payload(result)


@pytest.mark.parametrize("field", [
    "product", "strategy_version", "strategy_revision", "market_date", "next_trade_date",
    "v14_build_id", "v14_rule_revision", "close_confirmed", "market_phase",
])
def test_modern_signal_missing_required_field_is_rejected(field):
    result = payload()
    del result["signals"]["IM"][field]
    with pytest.raises(ValueError):
        digest.validate_success_payload(result)


def modern_payload(day, next_day, build, delivery, rule):
    result = payload()
    result.update(market_date=day, completed_day=day, verified_day=day,
                  next_trade_day=next_day, build=build, signal_build=build,
                  delivery_revision=delivery, signal_rule_revision=rule)
    for signal in result["signals"].values():
        signal.update(market_date=day, next_trade_date=next_day,
                      v14_build_id=build, v14_rule_revision=rule)
    if day >= "2026-10-08":
        result["grid_policy_revision"] = "ic_im_or_fear25_paired_exit50_20261008_v1"
        for signal in result["signals"].values():
            signal.update(grid_policy_revision=result["grid_policy_revision"],
                          grid_entry_source_current="none", grid_entry_source_target="none",
                          fear_greed_index=40., fear_data_date=day,
                          fear_data_status="same_day_post_close", fear_csv_sha256="a" * 64)
        result["signals"]["IC"].update(momentum_abs=.02, momentum_abs_days=40,
                                          momentum_abs_on=True, momentum_abs_debounce_active=False)
    return result


@pytest.mark.parametrize("day,next_day,build,delivery,rule", [
    ("2026-09-26", "2026-09-28", gate.FIX6_BUILD, gate.FIX6_DELIVERY_REVISION,
     "ic_im_v1_4_no_im_call_repeat_short_put_roll_20260926_v1"),
    ("2026-09-28", "2026-09-29", gate.EXPECTED_BUILD, gate.EXPECTED_DELIVERY_REVISION,
     "ic_im_v1_4_coreput3x_t1_open_20260928_v1"),
    ("2026-09-29", "2026-09-30", gate.FIX9_BUILD, gate.FIX9_DELIVERY_REVISION,
     "ic_im_v1_4_ordinaryput_open_ic_seller_mom120_20260929_v1"),
    ("2026-10-08", "2026-10-09", gate.FIX11_BUILD, gate.FIX11_DELIVERY_REVISION,
     "ic_im_v1_4_ic_csi500_ma105_w16_abs40_static_20261008_v1"),
])
def test_authoritative_forward_boundaries(day, next_day, build, delivery, rule):
    digest.validate_success_payload(modern_payload(day, next_day, build, delivery, rule))


@pytest.mark.parametrize("wrong", [None, "close", "phase", "anchor", 0, "False", "fear-unavailable"])
def test_modern_realtime_preserves_official_runner_semantics(wrong):
    result = modern_payload("2026-10-08", "2026-10-09", gate.FIX11_BUILD,
                            gate.FIX11_DELIVERY_REVISION,
                            "ic_im_v1_4_ic_csi500_ma105_w16_abs40_static_20261008_v1")
    result.update(publication_mode="realtime", completed_day="2026-09-30", verified_day="2026-09-30")
    for signal in result["signals"].values():
        signal.update(close_confirmed=False, market_phase="盘中", state_anchor_day="2026-09-30",
                      fear_data_status="intraday_provisional")
    if wrong is None:
        digest.validate_success_payload(result)
    else:
        signal = result["signals"]["IM"]
        if wrong == "close":
            signal["close_confirmed"] = True
        elif wrong == "phase":
            signal["market_phase"] = "收盘后"
        elif wrong == "anchor":
            signal["state_anchor_day"] = "2026-09-29"
        elif wrong == "fear-unavailable":
            for item in result["signals"].values():
                item["fear_data_status"] = "unavailable"
        else:
            signal["close_confirmed"] = wrong
        with pytest.raises(ValueError):
            digest.validate_success_payload(result)


def archived_payload():
    result = modern_payload("2026-09-25", "2026-09-28", gate.LEGACY_BUILD,
                            gate.LEGACY_DELIVERY_REVISION,
                            "ic_im_v1_4_iciv30_qdelta05_20260918_v1")
    result.pop("signal_build")
    result.pop("signal_rule_revision")
    for signal in result["signals"].values():
        for field in ("strategy_revision", "v14_build_id", "v14_rule_revision", "close_confirmed", "market_phase"):
            signal.pop(field)
    return result


def test_pre_fix6_archived_payload_keeps_its_legacy_field_contract():
    digest.validate_success_payload(archived_payload())


@pytest.mark.parametrize("field,value", [
    ("product", "IC"), ("v14_build_id", "wrong-version"),
    ("v14_rule_revision", "wrong-rule"), ("close_confirmed", False),
    ("market_phase", "盘中"), ("next_trade_date", "2026-09-29"),
])
def test_archive_may_omit_fields_but_cannot_supply_wrong_fields(field, value):
    result = archived_payload()
    result["signal_build"] = gate.LEGACY_BUILD
    result["signal_rule_revision"] = "ic_im_v1_4_iciv30_qdelta05_20260918_v1"
    result["signals"]["IM"][field] = value
    with pytest.raises(ValueError):
        digest.validate_success_payload(result)


def test_archive_preserves_actual_fix3_producer_in_simplified_delivery_tag():
    result = archived_payload()
    result.update(market_date="2026-09-18", completed_day="2026-09-18", verified_day="2026-09-18",
                  next_trade_day="2026-09-21",
                  signal_build="v1.4-20260918-r1-coreput3x-fixedshort95-fix3-iciv30-qdelta05",
                  signal_rule_revision="ic_im_v1_4_iciv30_qdelta05_20260918_v1")
    for signal in result["signals"].values():
        signal.update(market_date="2026-09-18", next_trade_date="2026-09-21",
                      v14_build_id=result["signal_build"], v14_rule_revision=result["signal_rule_revision"])
    digest.validate_success_payload(result)
    result["signals"]["IM"]["v14_build_id"] = "wrong-archive-version"
    with pytest.raises(ValueError, match="archived payload"):
        digest.validate_success_payload(result)
