"""Real-Git source activation; data and original restore evidence stay intact."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import activate_microcap_maintenance_runtime as runtime


@pytest.fixture
def prepared(tmp_path, request):
    root = tmp_path / "repo"
    root.mkdir()
    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.STDOUT).decode().strip()
    git("init", "-q")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Isolated test")
    sources = sorted(runtime.DATED_SHARE_SOURCES) if getattr(request, 'param', False) else [runtime.SOURCE]
    for name in sources:
        source = root / name
        source.parent.mkdir(exist_ok=True)
        source.write_bytes(("original " + name + "\n").encode())
    (root / "outputs").mkdir()
    (root / "outputs/nav.csv").write_bytes(b"approved history\n")
    git("add", ".")
    git("commit", "-qm", "baseline")
    old = git("rev-parse", "HEAD")
    old_hashes = {name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in sources}
    for name in sources:
        (root/name).write_bytes(("reviewed " + name + "\n").encode())
    git("add", ".")
    git("commit", "-qm", "reviewed fix")
    new = git("rev-parse", "HEAD")
    new_hashes = {name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in sources}
    git("remote", "add", "origin", str(root))
    git("checkout", "--detach", old)
    config = tmp_path / "config.json"
    config.write_text(json.dumps(dict(schema_version=1, baseline_sha=old, runtime_sha=new,
                                      changes={name: [old_hashes[name], new_hashes[name]] for name in sources})), encoding="utf8")
    restored = tmp_path / "restore.json"
    restored.write_text(json.dumps(dict(restored=True, strategy_sha=old)), encoding="utf8")
    return root, config, restored, tmp_path / "activation.json", git


@pytest.mark.parametrize('prepared', [False, True], indirect=True)
def test_verified_source_switch_preserves_data_and_original_receipt(prepared):
    root, config, restored, receipt, git = prepared
    original = restored.read_bytes()
    (root / "outputs/nav.csv").write_bytes(b"already restored actual state\n")
    result = runtime.activate(root, config, restored, receipt)
    assert result["activated"] is True and result["current_data_verified"] is False
    assert git("rev-parse", "HEAD") == json.loads(config.read_bytes())["runtime_sha"]
    assert (root / "outputs/nav.csv").read_bytes() == b"already restored actual state\n"
    assert restored.read_bytes() == original
    assert json.loads(receipt.read_bytes()) == result


@pytest.mark.parametrize('prepared', [False, True], indirect=True)
@pytest.mark.parametrize("fault", ["bad_hash", "wrong_baseline", "failed_restore", "wrong_restore_sha",
                                   "dirty_source", "unrelated_code", "overwrite_restore", "bad_schema", "unpaired_share"])
def test_unreviewed_or_unrestored_state_cannot_activate(prepared, fault):
    root, config, restored, receipt, git = prepared
    value = json.loads(config.read_bytes())
    if fault == "bad_hash": value["changes"][runtime.SOURCE][1] = "0" * 64
    elif fault == "wrong_baseline": value["baseline_sha"] = "a" * 40
    elif fault == "failed_restore": restored.write_text(json.dumps(dict(restored=False, strategy_sha=value["baseline_sha"])))
    elif fault == "wrong_restore_sha": restored.write_text(json.dumps(dict(restored=True, strategy_sha="a" * 40)))
    elif fault == "dirty_source": (root / runtime.SOURCE).write_text("uncommitted change")
    elif fault == "unrelated_code":
        git("checkout", "--detach", value["runtime_sha"])
        (root / "unrelated.py").write_text("unexpected change")
        git("add", ".")
        git("commit", "-qm", "unrelated change")
        value["runtime_sha"] = git("rev-parse", "HEAD")
        git("checkout", "--detach", value["baseline_sha"])
    elif fault == "overwrite_restore": receipt = restored
    elif fault == 'unpaired_share':
        value['changes']['scripts/share_unchanged_tail.py'] = ['1'*64, '2'*64]
        value['changes'].pop('scripts/realtime_state_bundle.py', None)
    else: value["schema_version"] = 2
    config.write_text(json.dumps(value), encoding="utf8")
    before = git("rev-parse", "HEAD")
    with pytest.raises(ValueError): runtime.activate(root, config, restored, receipt)
    assert git("rev-parse", "HEAD") == before


def test_workflow_activates_after_native_restore_before_refresh_and_rebalance():
    import yaml
    flow = yaml.safe_load((ROOT / ".github/workflows/microcap-member-maintenance.yml").read_text(encoding="utf8"))
    steps = flow["jobs"]["maintain"]["steps"]
    names = [step.get("name") for step in steps]
    assert names.index("Restore maintenance baseline") < names.index("Activate reviewed maintenance runtime")
    assert names.index("Activate reviewed maintenance runtime") < names.index("Refresh maintenance inputs")
    assert names.index("Activate reviewed maintenance runtime") < names.index("Build original close rebalance and three final streams")
    activation = next(step for step in steps if step.get("name") == "Activate reviewed maintenance runtime")
    assert "inputs.task" not in activation["if"]
    assert "continue-on-error" not in activation
    assert not any("send_gmail" in step.get("run", "") for step in steps)
    caches = [step for step in steps if step.get("uses", "").startswith("actions/cache/")]
    assert all("maintenance_sha" in step["with"]["key"] for step in caches)


def test_maintenance_refreshes_restored_current_names_without_enabling_history_rebuild():
    import yaml
    flow = yaml.safe_load((ROOT / ".github/workflows/microcap-member-maintenance.yml").read_text(encoding="utf8"))
    step = next(step for step in flow["jobs"]["maintain"]["steps"] if step.get("name") == "Refresh maintenance inputs")
    # Actual Nov9 counterexample: restored Nov5 current_st.csv still contained
    # ST美晨 after its already-approved Nov6 withdrawal. The native expired-
    # interval guard correctly refused it. Refresh both current source lists.
    assert "--force-refresh-static-inputs" in step["run"]
    assert "--allow-full-rebuild" not in step["run"]
    assert "--force-refresh " not in step["run"]
    assert "continue-on-error" not in step
