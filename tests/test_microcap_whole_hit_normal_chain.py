"""Execute real workflow conditions for the whole-hit normal validation path.

This is a condition/transition regression, not a cloud execution or market proof.
"""
import ast
from pathlib import Path
import re

import pytest
import yaml


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/microcap-realtime-digest.yml"
JOBS = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
STEPS = {step.get("name"): step for step in JOBS["send"]["steps"]}


def condition(expression, values, *, cancelled=False):
    # Evaluate only the boolean/comparison subset used by these actual gates;
    # reject unsupported syntax rather than silently treating it as successful.
    expression = expression.removeprefix("${{").removesuffix("}}").strip()
    expression = expression.replace("!cancelled()", repr(not cancelled))
    expression = expression.replace("always()", "True").replace("&&", " and ").replace("||", " or ")
    def token(match):
        value = match.group()
        if value.startswith("'"):
            return value
        if value in ("true", "false"):
            return value.title()
        return repr(values.get(value, ""))
    expression = re.sub(r"'[^']*'|(?:steps|inputs|needs|github)\.[A-Za-z0-9_.-]+|\b(?:true|false)\b", token, expression)
    tree = ast.parse(expression, mode="eval")
    allowed = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.Compare, ast.Eq, ast.NotEq, ast.Constant)
    assert all(isinstance(node, allowed) for node in ast.walk(tree)), expression
    return bool(eval(compile(tree, "<actual-workflow-condition>", "eval"), {"__builtins__": {}}, {}))


@pytest.mark.parametrize("mode", ["bootstrap", "require_hit"])
def test_cancelled_recovery_cannot_run_even_with_successful_regression(mode):
    values = {"github.event_name": "workflow_dispatch", "inputs.validation_only": False,
              "inputs.recovery_check": mode, "needs.regression.result": "success"}
    expression = JOBS["recovery-check"]["if"]
    assert condition(expression, values, cancelled=False)
    assert not condition(expression, values, cancelled=True)


def baseline():
    return {"inputs.validation_only": True, "inputs.recovery_check": "off",
            "inputs.approved_state_url": "", "inputs.delivery_test_id": "",
            "inputs.correction": False, "inputs.external_schedule": False,
            "github.event_name": "workflow_dispatch", "needs.regression.result": "success",
            "needs.check-trading-day.result": "success",
            "needs.check-trading-day.outputs.SHOULD_RUN_MICROCAP": "true",
            "steps.delivery_gate.outputs.should_send": "true",
            "steps.publication_mode.outputs.mode": "close_confirmed",
            "steps.whole_state_restore.outputs.restored": "true",
            "steps.whole_state_restore.outcome": "success",
            "steps.recovered_full_cache.outcome": "success",
            "steps.recovered_full_cache.outputs.validated": "true"}


def allowed(name, values):
    return condition(STEPS[name]["if"], values)


def test_whole_hit_reaches_normal_refresh_signals_pack_digest_without_smtp():
    values = baseline()
    assert condition(JOBS["send"]["if"], values)
    assert not condition(JOBS["recovery-check"]["if"], values)
    for name in ("Restore verified production state bundle", "Restore durable verified production state bundle",
                 "Restore approved release fallback"):
        assert not allowed(name, values)
    assert allowed("Validate recovered full rebalance cache", values)
    assert allowed("Resolve same-day static refresh mode", values)
    assert allowed("Refresh Top100 realtime state", values)
    values.update({"steps.refresh_state.outcome": "success", "steps.refresh_state.outputs.validated": "true"})
    assert not allowed("Record refresh failure for digest", values)
    assert allowed("Prepare shared publication state", values)
    values["steps.state_bundle.outcome"] = "success"
    assert allowed("Capture shared inputs before three-version generation", values)
    values["steps.shared_inputs_before.outcome"] = "success"
    for version in ("20", "23", "25"):
        assert allowed(f"Run v{version[0]}.{version[1]} selected signal", values)
        values[f"steps.signals_v{version}.outputs.validated"] = "true"
    assert allowed("Check shared inputs after three-version generation", values)
    values["steps.shared_inputs_after.outcome"] = "success"
    whole = next(step for step in STEPS.values() if step.get("id") == "whole_delivery")
    assert condition(whole["if"], values)
    values["steps.whole_delivery.outputs.validated"] = "true"
    assert allowed("Verify current share-source disclosures", values)
    assert allowed("Build Markdown digest", values)
    values.update({"steps.digest.outputs.status": "OK", "steps.digest.outcome": "success"})
    for name in ("Persist normal-send intent before SMTP", "Send Gmail", "Preserve accepted SMTP receipt",
                 "Prepare delivery marker", "Mark digest delivered", "Persist close-confirmed whole recovery state"):
        assert not allowed(name, values), name
    artifact = next(step for step in STEPS.values()
                    if "microcap-whole-delivery-validation-state" in str(step.get("with", {}).get("name", "")))
    assert condition(artifact["if"], values)


@pytest.mark.parametrize("key,value", [
    ("steps.delivery_gate.outputs.should_send", "false"),
    ("steps.recovered_full_cache.outputs.validated", "false"),
    ("steps.recovered_full_cache.outputs.validated", ""),
    ("steps.whole_state_restore.outputs.restored", "false"),
    ("steps.whole_state_restore.outputs.restored", ""),
])
def test_whole_hit_does_not_bypass_delivery_cache_or_restore_gates(key, value):
    values = baseline()
    values[key] = value
    assert not allowed("Refresh Top100 realtime state", values)


def test_failed_refresh_after_whole_hit_cannot_reach_signals_or_pack():
    values = baseline()
    values.update({"steps.refresh_state.outcome": "success", "steps.refresh_state.outputs.validated": "false"})
    assert allowed("Record refresh failure for digest", values)
    assert not allowed("Prepare shared publication state", values)
    for version in ("2.0", "2.3", "2.5"):
        assert not allowed(f"Run v{version} selected signal", values)
    whole = next(step for step in STEPS.values() if step.get("id") == "whole_delivery")
    assert not condition(whole["if"], values)
