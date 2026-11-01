"""Process startup compatibility for the exact approved CRLF CSV seed."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.parametrize("enabled", [False, True])
def test_csv_serialization_in_fresh_subprocess(enabled):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "scripts/microcap_csv_compat")
    env.pop("MICROCAP_CSV_LINE_TERMINATOR", None)
    if enabled:
        env["MICROCAP_CSV_LINE_TERMINATOR"] = "crlf"
    code = "import json,os,pandas as pd; print(json.dumps({'sep':os.linesep,'csv':pd.DataFrame({'a':[1]}).to_csv(index=False)}))"
    result = json.loads(subprocess.check_output([sys.executable, "-B", "-c", code], env=env, text=True))
    expected = "\r\n" if enabled else os.linesep
    assert result == {"sep": expected, "csv": "a" + expected + "1" + expected}

def test_compatibility_is_scoped_to_delivery_job():
    workflow = yaml.safe_load((ROOT / ".github/workflows/microcap-realtime-digest.yml").read_text(encoding="utf-8"))
    assert workflow["jobs"]["send"]["env"]["MICROCAP_CSV_LINE_TERMINATOR"] == "crlf"
    paths = workflow["jobs"]["send"]["env"]["PYTHONPATH"].split(":")
    assert paths == ["${{ github.workspace }}/automation/scripts/microcap_csv_compat", "${{ github.workspace }}/microcap"]
    for name, job in workflow["jobs"].items():
        if name != "send":
            assert "MICROCAP_CSV_LINE_TERMINATOR" not in job.get("env", {})
