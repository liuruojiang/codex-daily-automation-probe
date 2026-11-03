import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/check_microcap_shared_inputs.py"


@pytest.fixture
def isolated_strategy(tmp_path):
    root = tmp_path / "strategy"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "__init__.py").write_text("")
    # The helper must import the selected strategy, never production caches.
    (scripts / "top100_delivery.py").write_text(
        "import hashlib\ndef input_hashes(root):\n"
        " return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() "
        "for p in root.glob('*.csv')}\n")
    (root / "base.csv").write_text("date,close\n2026-11-02,100\n")
    return root, tmp_path / "before.json"


def invoke(command, root, snapshot, day="2026-11-02"):
    return subprocess.run([sys.executable, "-B", str(HELPER), command,
                           "--root", str(root), "--snapshot", str(snapshot),
                           "--expected-date", day], capture_output=True, text=True)


def test_same_input_siblings_pass_and_snapshot_is_not_rewritten(isolated_strategy):
    root, snapshot = isolated_strategy
    assert invoke("capture", root, snapshot).returncode == 0
    before = snapshot.read_bytes()
    assert invoke("check", root, snapshot).returncode == 0
    assert snapshot.read_bytes() == before
    assert invoke("capture", root, snapshot).returncode != 0


@pytest.mark.parametrize("change", ["same_day_value", "added", "removed", "date", "missing", "malformed"])
def test_changed_or_unbound_sibling_inputs_fail_closed(isolated_strategy, change):
    root, snapshot = isolated_strategy
    assert invoke("capture", root, snapshot).returncode == 0
    day = "2026-11-02"
    if change == "same_day_value":
        (root / "base.csv").write_text("date,close\n2026-11-02,101\n")
    elif change == "added":
        (root / "another.csv").write_text("new input")
    elif change == "removed":
        (root / "base.csv").unlink()
    elif change == "date":
        day = "2026-11-03"
    elif change == "missing":
        snapshot.unlink()
    elif change == "malformed":
        snapshot.write_text(json.dumps({"inputs": {}}))
    assert invoke("check", root, snapshot, day).returncode != 0


def test_workflow_blocks_pack_when_generation_inputs_change():
    workflow = yaml.safe_load((ROOT / ".github/workflows/microcap-realtime-digest.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["send"]["steps"]
    by_id = {step.get("id"): step for step in steps}
    before, after = by_id["shared_inputs_before"], by_id["shared_inputs_after"]
    assert "steps.state_bundle.outcome == 'success'" in before["if"]
    for name in ("signals_v20", "signals_v23", "signals_v25"):
        step = by_id[name]
        assert steps.index(before) < steps.index(step) < steps.index(after)
        assert "steps.shared_inputs_before.outcome == 'success'" in step["if"]
        assert f"steps.{name}.outputs.validated == 'true'" in after["if"]
    assert steps.index(after) < steps.index(by_id["whole_delivery"])
    assert "steps.shared_inputs_after.outcome == 'success'" in by_id["whole_delivery"]["if"]
    for step in (before, after):
        assert "close_confirmed" in step["if"]
        assert '--snapshot "${RUNNER_TEMP}/microcap-shared-inputs-before.json"' in step["run"]


def test_actual_failed_input_check_marks_all_digest_versions_failed(tmp_path):
    from test_microcap_refresh_permanent_failure import execute_shell
    workflow = yaml.safe_load((ROOT / ".github/workflows/microcap-realtime-digest.yml").read_text(encoding="utf-8"))
    step = next(s for s in workflow["jobs"]["send"]["steps"] if s.get("id") == "shared_inputs_after")
    script = step["run"].replace("${{ steps.market_target.outputs.date }}", "2026-11-02")
    # Exercise the production shell failure path; continue only in this harness
    # after it exits so that the observed environment can be checked.
    execute_shell('GITHUB_ENV=env\nRUNNER_TEMP=.\npython() { return 7; }\n(\n' + script + '\n)\nstatus=$?\n[[ "$status" -eq 7 ]]\n', tmp_path)
    assert (tmp_path / "env").read_text().splitlines() == [
        f"SIGNAL_V2_{v}_EXIT_CODE=shared_inputs_changed" for v in (0, 3, 5)]
