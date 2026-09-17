"""No-mail acceptance must still exercise refresh and final delivery artifacts."""
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_microcap_delivery as gate

WORKFLOW = ROOT / ".github/workflows/microcap-realtime-digest.yml"


def workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


@pytest.mark.parametrize("correction", [False, True])
def test_validation_bypasses_existing_delivery_state_without_network(tmp_path, correction):
    output = tmp_path / "output"
    argv = ["check_microcap_delivery.py", "--validation-only", "--publication-mode", "close_confirmed"]
    if correction:
        argv.append("--correction")
    with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}, clear=True), patch.object(sys, "argv", argv), patch.object(gate, "fetch_artifacts", side_effect=AssertionError("validation must not inspect delivery markers")):
        assert gate.main() == 0
    values = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
    assert values["should_send"] == "true"  # Existing build route; mail is gated separately.
    assert values["recover_marker"] == "false"
    assert values["validation_only"] == "true"


def test_validation_input_defaults_to_normal_scheduled_behavior():
    document = workflow()
    # PyYAML 1.1 decodes the Actions key 'on' as True.
    on = document.get("on", document.get(True))
    option = on["workflow_dispatch"]["inputs"]["validation_only"]
    assert option["type"] == "boolean"
    assert option["default"] is False


def test_every_mail_and_delivery_write_is_independently_disabled():
    steps = workflow()["jobs"]["send"]["steps"]
    guarded = {
        "Recover delivery marker from accepted SMTP receipt",
        "Mark recovered digest delivered",
        "Persist normal-send intent before SMTP",
        "Send Gmail",
        "Preserve accepted SMTP receipt",
        "Prepare delivery marker",
        "Mark digest delivered",
    }
    found = set()
    for step in steps:
        name = step.get("name")
        sensitive = (
            "send_report.py" in step.get("run", "")
            or "delivery-marker/" in step.get("run", "")
            or "marker_name" in str(step.get("with", {}))
        )
        if name in guarded or sensitive:
            assert name in guarded, f"New delivery side effect must be explicitly reviewed: {name}"
            assert step["if"].startswith("inputs.validation_only != true && ")
            found.add(name)
    assert found == guarded


def test_validation_keeps_actual_refresh_consumers_and_final_artifacts():
    steps = {step.get("name"): step for step in workflow()["jobs"]["send"]["steps"]}
    for name in (
        "Refresh Top100 realtime state", "Run v2.0 selected signal",
        "Run v2.3 selected signal", "Run v2.5 selected signal",
        "Verify and pack all three final deliveries", "Upload signal outputs",
        "Fail job when signal publication failed",
    ):
        assert "inputs.validation_only != true" not in steps[name].get("if", "")
    gate_step = steps["Check delivery marker"]
    assert "inputs.validation_only" in gate_step["env"]["VALIDATION_ONLY"]
    assert 'args+=(--validation-only)' in gate_step["run"]
    archive = steps["Preserve verified whole delivery for the next trading day"]
    assert "inputs.validation_only == true || steps.send_gmail.outcome == 'success'" in archive["if"]
    assert "steps.whole_delivery.outputs.exit_code == '0'" in archive["if"]
    assert "steps.digest.outputs.status == 'OK'" in archive["if"]


def test_validation_artifact_cannot_be_consumed_as_production_state():
    steps = {step.get("name"): step for step in workflow()["jobs"]["send"]["steps"]}
    name = steps["Preserve verified whole delivery for the next trading day"]["with"]["name"]
    assert "inputs.validation_only == true && 'microcap-whole-delivery-validation-state'" in name
    assert "|| 'microcap-whole-delivery-state'" in name


def test_validation_cannot_persist_production_cache_or_recovery_artifacts():
    for step in workflow()["jobs"]["send"]["steps"]:
        if step.get("uses", "").startswith("actions/cache/save@"):
            assert step["if"].startswith("inputs.validation_only != true && ")
        if step.get("name") == "Preserve validated state bundle for long-gap recovery":
            assert "inputs.validation_only == true && 'microcap-verified-state-validation'" in step["with"]["name"]


def test_durable_recovery_uses_actual_automation_checkout_path():
    steps = {step.get("name"): step for step in workflow()["jobs"]["send"]["steps"]}
    assert steps["Check out automation repository"]["with"]["path"] == "automation"
    command = steps["Restore durable verified production state bundle"]["run"]
    assert "python automation/scripts/restore_microcap_verified_state.py" in command
