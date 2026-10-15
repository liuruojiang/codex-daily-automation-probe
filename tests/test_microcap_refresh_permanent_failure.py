"""Execute the production workflow's failure-control shell, never a strategy run.

The lineage diagnostics below are emitted by the real strategy source and by
run 38702653906. Transport samples are control inputs, not freshness evidence.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/microcap-realtime-digest.yml"


def refresh_script() -> str:
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    return next(step["run"] for step in workflow["jobs"]["send"]["steps"]
                if step.get("name") == "Refresh Top100 realtime state")


def bash_executable() -> str:
    executable = shutil.which("bash")
    if not executable and os.name == "nt":
        git = shutil.which("git")
        candidate = Path(git).resolve().parents[1] / "bin/bash.exe" if git else None
        if candidate and candidate.is_file():
            executable = str(candidate)
    assert executable, "Bash is required to execute the actual GitHub workflow control"
    return executable


def execute_shell(script: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [bash_executable(), "--noprofile", "--norc", "-c", script],
        cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=15,
    )
    assert result.returncode == 0, result.stderr
    return result


def retry_decision(tmp_path: Path, diagnostic: str, status: int = 1):
    script = refresh_script()
    start = script.index('if [[ "${status}" -eq 0 ]]; then')
    end = script.index('sleep $(( attempt < 6 ? attempt * 20 : 120 ))', start)
    # The exact production decision is run inside a one-iteration harness. The
    # marker only tells whether execution reaches the unchanged retry backoff.
    decision = script[start:end]
    (tmp_path / "attempt.log").write_text(diagnostic, encoding="utf-8")
    result = execute_shell(
        f'status={status}\nattempt_log=attempt.log\nfor once in 1; do\n'
        + decision + '\nprintf "retry_reached\\n"\ndone\n'
        + 'printf "exit_status=%s\\n" "${status}"\n', tmp_path,
    )
    return result.stdout


@pytest.mark.parametrize("diagnostic", [
    "SecurityMetaHistoryCorrectionRequired: Security metadata historical intervals changed for 000677; an exact-hash lineage review is required before replacement",
    "SecurityMetaHistoryCorrectionRequired: Security metadata identity changed for 301139 while history sources failed; an exact-hash lineage review is required before replacement",
    "Security metadata historical intervals changed for 002193",
    "Security metadata identity changed for 301139",
    "RuntimeError: Exact frozen proxy seed could not be extended through the no-new-rebalance tail; a narrow runner cache is not authorized for a historical rebuild.",
    "RuntimeError: Exact frozen proxy seed tail was extended, but the costed NAV splice failed its anchor or verification checks",
    "RuntimeError: state refresh failed: proxy metadata fingerprint mismatch: expected=6496cc19, actual=0dc06e4d",
    "RuntimeError: costed NAV historical rewrite detected on frozen dates; examples: []",
    "RuntimeError: costed NAV historical rewrite audit missing key columns: date",
    "SecurityMetaHistoryCorrectionRequired: changed history\nTimeoutError: later request timed out",
])
def test_actual_workflow_stops_permanent_lineage_rejection(tmp_path, diagnostic):
    output = retry_decision(tmp_path, diagnostic, status=17)
    assert "retry_reached" not in output
    assert "Non-retryable lineage or frozen-history rejection" in output
    assert "exit_status=17" in output
    assert "Non-retryable" in (tmp_path / "realtime_state_refresh_result.txt").read_text()


@pytest.mark.parametrize("diagnostic", [
    "requests.exceptions.ReadTimeout: provider read timed out",
    "ConnectionError: temporary connection reset by peer",
    "HTTPError: 503 Service Unavailable",
])
def test_actual_workflow_keeps_transient_transport_retry(tmp_path, diagnostic):
    output = retry_decision(tmp_path, diagnostic, status=1)
    assert "retry_reached" in output
    assert "exit_status=1" in output
    assert "Non-retryable" not in output
    assert not (tmp_path / "realtime_state_refresh_result.txt").exists()


def test_actual_zero_exit_control_does_not_retry(tmp_path):
    # This checks shell control only; it does not claim a real refresh passed.
    output = retry_decision(tmp_path, "control-only empty diagnostic", status=0)
    assert "retry_reached" not in output
    assert "exit_status=0" in output
    assert "Non-retryable" not in output


@pytest.mark.parametrize("status, conclusion, title", [
    (17, "failure", "Microcap Top100 refresh blocked"),
    (0, "success", "Microcap Top100 refresh complete"),
])
def test_actual_progress_payload_matches_refresh_exit(tmp_path, status, conclusion, title):
    script = refresh_script()
    start = script.index("# Close the progress check with the actual refresh outcome.")
    # Execute the exact payload generation but stop before the external PATCH.
    end = script.index("  curl --silent --show-error --fail-with-body", start)
    payload_script = script[start:end] + "fi\n"
    python = shlex.quote(Path(sys.executable).as_posix())
    execute_shell(
        f'python() {{ {python} "$@"; }}\nstatus={status}\n'
        'progress_check_id=control-only\nattempt=1\nstarted_epoch=$(date +%s)\n'
        'export GITHUB_SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n'
        + payload_script, tmp_path,
    )
    payload = json.loads((tmp_path / "progress_check_payload.json").read_text())
    assert payload["status"] == "completed"
    assert payload["conclusion"] == conclusion
    assert payload["output"]["title"] == title
    assert f"refresh_exit_code={status}" in payload["output"]["summary"]
