"""Cross-run recovery contracts, supplemented by real cloud restore acceptance."""
import io
import json
from pathlib import Path
import subprocess
import sys
import zipfile
from unittest.mock import patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import microcap_whole_state_recovery as recovery

SHA = "a" * 40


def make_bundle(tmp_path, **changes):
    manifest = dict(status="complete", ok=True, scope="whole_workspace_delivery",
                    expected_date="2026-11-02", release_sha=SHA)
    manifest.update(changes)
    path = tmp_path / recovery.BUNDLE_NAME
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("outputs/top100_delivery_manifest.json", json.dumps(manifest))
    return path


@pytest.mark.parametrize("changes", [dict(release_sha="b" * 40), dict(status="blocked"),
                                   dict(ok=False), dict(scope="base_state_only"),
                                   dict(expected_date="2026-11-04")])
def test_wrong_identity_incomplete_and_future_archives_never_reach_restore(tmp_path, changes):
    with patch.object(recovery.subprocess, "run") as run, pytest.raises(ValueError):
        recovery.restore_bundle(tmp_path, make_bundle(tmp_path, **changes), SHA, "2026-11-03")
    run.assert_not_called()


def test_official_restore_failure_is_not_reported_as_recovered(tmp_path):
    with patch.object(recovery.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "restore")):
        with pytest.raises(subprocess.CalledProcessError):
            recovery.restore_bundle(tmp_path, make_bundle(tmp_path), SHA, "2026-11-03")


def test_archive_seed_uses_own_date_not_current_target(tmp_path):
    with patch.object(recovery.subprocess, "run") as run:
        result = recovery.restore_bundle(tmp_path, make_bundle(tmp_path), SHA, "2026-11-03")
    command = run.call_args.args[0]
    assert command[-2:] == ["--expected-date", "2026-11-02"]
    assert result["restored"] is True
    assert result["freshness"] == "verified_archive_only_requires_current_refresh"


def test_discovery_excludes_validation_other_pins_and_failed_runs(monkeypatch):
    name = recovery.artifact_name(SHA)
    def get(req, **kwargs):
        if "/artifacts?" in req.full_url:
            items = [dict(id=i, name=n, expired=False, created_at=str(i), size_in_bytes=500,
                          workflow_run={"id": i},
                          archive_download_url=f"https://api.invalid/repos/o/r/actions/artifacts/{i}/zip")
                     for i, n in [(5, "microcap-whole-delivery-validation-state"),
                                  (4, recovery.artifact_name("b" * 40)), (3, name), (2, name), (1, name)]]
            payload = {"artifacts": items}
        else:
            i = int(req.full_url.rsplit("/", 1)[1])
            payload = dict(id=i, status="completed", conclusion="success" if i < 3 else "failure",
                           head_branch="main", path="wrong.yml" if i == 2 else recovery.eligible_run.__globals__["WORKFLOW_PATH"])
        class Response(io.BytesIO):
            pass
        return Response(json.dumps(payload).encode())
    monkeypatch.setattr(recovery.urllib.request, "urlopen", get)
    assert recovery.fetch_latest("o/r", "fake", SHA, "https://api.invalid")["id"] == 1


def test_required_next_run_miss_fails_instead_of_bootstrapping(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["recovery", "restore", "--root", str(tmp_path),
        "--bundle", str(tmp_path / recovery.BUNDLE_NAME), "--strategy-sha", SHA,
        "--not-after-date", "2026-11-03", "--receipt", str(tmp_path / "receipt.json"), "--require-hit"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setattr(recovery.subprocess, "check_output", lambda *a, **k: SHA)
    monkeypatch.setattr(recovery, "fetch_latest", lambda *a: None)
    with pytest.raises(RuntimeError, match="was not found"):
        recovery.main()
    assert not (tmp_path / "receipt.json").exists()


def test_producer_consumer_names_match_and_production_reads_whole_before_legacy():
    jobs = yaml.safe_load((ROOT / ".github/workflows/microcap-realtime-digest.yml").read_text(encoding="utf-8"))["jobs"]
    steps = jobs["send"]["steps"]
    named = {s.get("name"): s for s in steps}
    consumer = named["Restore close-confirmed whole delivery"]
    producer = named["Persist close-confirmed whole recovery state"]
    assert producer["with"]["name"].startswith(recovery.PREFIX)
    assert producer["if"].startswith("inputs.validation_only != true && ")
    assert "steps.whole_delivery.outputs.validated == 'true'" in producer["if"]
    assert "send_gmail" not in producer["if"]
    assert steps.index(consumer) < steps.index(named["Restore verified production state bundle"])
    assert steps.index(producer) < steps.index(named["Send Gmail"])
    for name in ["Restore verified production state bundle", "Restore durable verified production state bundle", "Restore approved release fallback"]:
        assert "steps.whole_state_restore.outputs.restored != 'true'" in named[name]["if"]
    for name in ["Validate recovered full rebalance cache", "Resolve same-day static refresh mode"]:
        assert "steps.whole_state_restore.outputs.restored == 'true'" in named[name]["if"]
    probe = jobs["recovery-check"]
    assert "inputs.validation_only != true" in probe["if"]
    assert "inputs.recovery_check" in jobs["send"]["if"]
    probe_steps = {s.get("name"): s for s in probe["steps"]}
    assert probe_steps["Persist close-confirmed whole recovery state"]["with"]["name"].startswith(recovery.PREFIX)
    assert "--require-hit" in probe_steps["Restore close-confirmed whole delivery"]["run"]
    for step in probe["steps"]:
        assert "send_report.py" not in step.get("run", "")
        assert "realtime_state_bundle.py refresh" not in step.get("run", "")
