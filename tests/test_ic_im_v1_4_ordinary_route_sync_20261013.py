"""Synthetic faults at the public mail builder; no market observations or sends.

The producer owns core route switches and 3x execution separately from the
ordinary momentum T+1 plan. Both accepted and forged plans enter build_success.
"""
from copy import deepcopy

import pytest

from test_ic_im_v1_4_contract_adversarial_20261009 import digest, valid_fix11_payload


def leg(contract, security_id, qty, *, new_contract=None, new_security_id=None, new_qty=None):
    if new_qty is None:
        new_contract, new_security_id, new_qty = contract, security_id, qty
    return dict(old_contract=contract, old_security_id=security_id, old_qty=qty,
                new_contract=new_contract, new_security_id=new_security_id, new_qty=new_qty,
                changed=contract != new_contract or qty != new_qty)


def scenario(product, stage):
    result = valid_fix11_payload()
    signal = result["signals"][product]
    if product == "IC":
        old, new, momentum = "510500P2612M07500", "510500P2703M07000", "510500P2612M07250"
        old_id, new_id, momentum_id = "10012099", "10012100", "10012101"
        old_qty, new_qty, momentum_qty = 10., 12., 3.
    else:
        old, new, momentum = "MO2612-P-7600", "MO2703-P-7400", "MO2612-P-7200"
        old_id = new_id = momentum_id = None
        old_qty, new_qty, momentum_qty = 1.5, 1.5, 1.
    core = leg(old, old_id, old_qty)
    signal.update(momentum_next_weight=1., momentum_units_target=.5,
                  momentum_units_change=.5, total_units_target=1., total_units_change=.5,
                  v14_action="HOLD")
    if product == "IC":
        signal.update(put_target_momentum_qty=momentum_qty, put_target_total_qty=old_qty + momentum_qty,
                      put_target_contract=momentum, put_target_security_id=momentum_id)
    else:
        signal.update(momentum_put_target_qty_normalized=momentum_qty,
                      total_put_target_qty_normalized=old_qty + momentum_qty,
                      momentum_put_target_contract=momentum)

    if stage in {"short_put", "assigned_etf", "cash_wait", "recovery_future"}:
        signal.update(v14_route_state=stage,
                      v14_action="ENTER_SHORT_PUT" if stage == "short_put" else "HOLD_RECOVERY",
                      core_units_target=.5 if stage == "recovery_future" else 0.,
                      total_units_target=1. if stage == "recovery_future" else .5,
                      total_units_change=.5 if stage == "recovery_future" else 0.,
                      v14_core_put_contract=None, v14_core_put_security_id=None, v14_core_put_qty=0.)
        if stage == "short_put":
            signal.update(v14_short_put_contract=momentum, v14_short_put_qty_normalized=1.5)
        if product == "IC":
            signal.update(put_target_core_qty=0., put_target_total_qty=momentum_qty)
        else:
            signal.update(core_put_target_qty_normalized=0., core_put_target_contract=None,
                          total_put_target_qty_normalized=momentum_qty)
        core = leg(None, None, 0.)
    elif stage in {"pending", "waiting"}:
        signal.update(v14_action="CORE_PUT_PROFIT3X_REENTER" if stage == "pending" else "HOLD_FAIL_CLOSED",
                      v14_profit_pending=True, v14_profit_trigger_day=result["market_date"],
                      v14_profit_execution_day=result["next_trade_day"],
                      v14_profit_old_contract=old, v14_profit_old_security_id=old_id,
                      v14_profit_old_qty=old_qty, v14_profit_reentry_contract=new,
                      v14_profit_reentry_security_id=new_id, v14_profit_reentry_qty=new_qty,
                      v14_profit_reentry_status="scheduled_t_plus_1_open")
    elif stage == "executed":
        signal.update(v14_action="EXECUTE_CORE_PUT_PROFIT3X_REENTER",
                      v14_profit_reentry_status="confirmed_open_research_price",
                      v14_profit_open_price_day=result["market_date"],
                      v14_profit_open_old_contract=old, v14_profit_open_old_qty=old_qty,
                      v14_profit_open_executed_contract=new, v14_profit_open_executed_qty=new_qty,
                      v14_profit_exit_open_price=400., v14_profit_reentry_entry_premium=100.,
                      v14_profit_open_old_source="synthetic_dated_open",
                      v14_profit_open_new_source="synthetic_dated_open")
        if product == "IC":
            signal.update(v14_core_put_contract=new, v14_core_put_security_id=new_id,
                          v14_core_put_qty=new_qty, put_target_core_qty=new_qty,
                          put_target_total_qty=new_qty + momentum_qty)
        else:
            signal.update(core_put_target_contract=new, core_put_target_qty_normalized=new_qty,
                          total_put_target_qty_normalized=new_qty + momentum_qty)
        core = leg(new, new_id, new_qty)
    else:
        assert stage == "future"
    signal.update(v14_ordinary_put_plan_status="scheduled_t_plus_1_open",
                  v14_ordinary_put_pending={
                      "product": product, "signal_day": result["market_date"],
                      "execution_day": result["next_trade_day"],
                      "legs": {"core": core, "momentum": leg(None, None, 0., new_contract=momentum,
                                                           new_security_id=momentum_id, new_qty=momentum_qty)},
                  })
    return result


@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("stage", ["future", "short_put", "assigned_etf", "cash_wait",
                                    "recovery_future", "pending", "waiting", "executed"])
def test_public_builder_accepts_independent_momentum_plan_at_each_core_stage(product, stage):
    result = scenario(product, stage)
    before = deepcopy(result)
    subject, body, actionable = digest.build_success(result, "", "")
    assert subject and "普通核心/动量买Put次日开盘计划" in body and actionable
    assert result == before


@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("stage", ["short_put", "recovery_future", "pending", "executed"])
@pytest.mark.parametrize("fault", ["contract", "quantity", "changed", "momentum"])
def test_public_builder_rejects_forged_leg_despite_unchanged_core_label(product, stage, fault):
    result = scenario(product, stage)
    legs = result["signals"][product]["v14_ordinary_put_pending"]["legs"]
    if fault == "contract":
        # Even two equal forged identities with unchanged=False are invalid.
        legs["core"]["old_contract"] = legs["core"]["new_contract"] = "FORGED"
    elif fault == "quantity":
        legs["core"]["old_qty"] += 1
        legs["core"]["new_qty"] += 1
    elif fault == "changed":
        legs["core"]["changed"] = True
    else:
        legs["momentum"]["new_contract"] = "FORGED"
    with pytest.raises(ValueError):
        digest.build_success(result, "", "")


@pytest.mark.parametrize("stage", ["short_put", "pending", "executed"])
@pytest.mark.parametrize("side", ["old", "new"])
def test_ic_security_id_cannot_be_forged_in_locked_core(stage, side):
    result = scenario("IC", stage)
    result["signals"]["IC"]["v14_ordinary_put_pending"]["legs"]["core"][f"{side}_security_id"] = "FORGED"
    with pytest.raises(ValueError):
        digest.build_success(result, "", "")


@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("field,value", [
    ("v14_profit_reentry_status", "scheduled_t_plus_1_open"),
    ("v14_profit_open_price_day", "2026-09-30"),
    ("v14_profit_exit_open_price", None),
    ("v14_profit_reentry_entry_premium", float("nan")),
    ("v14_profit_open_executed_contract", "FORGED"),
    ("v14_profit_open_executed_qty", 999.),
    ("v14_profit_open_old_contract", "FORGED"),
    ("v14_profit_open_old_qty", 999.),
])
def test_execution_label_does_not_bypass_confirmed_open_evidence(product, field, value):
    result = scenario(product, "executed")
    result["signals"][product][field] = value
    with pytest.raises(ValueError):
        digest.build_success(result, "", "")


@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("stage", ["pending", "executed", "recovery_future"])
def test_monthly_maintenance_still_rejects_ordinary_plan(product, stage):
    result = scenario(product, stage)
    result["signals"][product]["option_monthly_reset_due"] = True
    with pytest.raises(ValueError):
        digest.build_success(result, "", "")


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_pending_plan_cannot_pretend_future_reentry_is_already_held(product):
    result = scenario(product, "pending")
    signal = result["signals"][product]
    signal["v14_ordinary_put_pending"]["legs"]["core"] = leg(
        signal["v14_profit_reentry_contract"], signal.get("v14_profit_reentry_security_id"),
        signal["v14_profit_reentry_qty"])
    with pytest.raises(ValueError):
        digest.build_success(result, "", "")


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_nonfuture_route_cannot_hide_positive_signal_core_target(product):
    result = scenario(product, "recovery_future")
    signal = result["signals"][product]
    if product == "IC":
        signal.update(put_target_core_qty=1., put_target_total_qty=signal["put_target_total_qty"] + 1.)
    else:
        signal.update(core_put_target_qty_normalized=1.,
                      total_put_target_qty_normalized=signal["total_put_target_qty_normalized"] + 1.)
    with pytest.raises(ValueError):
        digest.build_success(result, "", "")


@pytest.mark.parametrize("field,value", [
    ("v14_core_put_qty", 999.), ("v14_core_put_contract", None),
    ("v14_core_put_security_id", None),
])
def test_ic_executed_core_uses_dedicated_identity_and_quantity(field, value):
    result = scenario("IC", "executed")
    result["signals"]["IC"][field] = value
    with pytest.raises(ValueError):
        digest.build_success(result, "", "")


def test_ic_split_current_security_ids_are_checked_against_the_correct_leg():
    result = scenario("IC", "future")
    signal = result["signals"]["IC"]
    signal.update(put_current_contract=signal["put_target_contract"],
                  put_current_security_id=signal["put_target_security_id"],
                  put_current_momentum_qty=2., put_current_total_qty=12.,
                  momentum_current_weight=1., momentum_units_current=.5,
                  momentum_units_change=0., total_units_current=1., total_units_change=0.)
    signal["v14_ordinary_put_pending"]["legs"]["momentum"].update(
        old_contract=signal["put_current_contract"], old_security_id=signal["put_current_security_id"], old_qty=2.)
    digest.build_success(result, "", "")
    signal["v14_ordinary_put_pending"]["legs"]["core"]["old_security_id"] = signal["put_current_security_id"]
    with pytest.raises(ValueError):
        digest.build_success(result, "", "")
