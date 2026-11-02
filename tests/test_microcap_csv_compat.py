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

def test_native_daily_job_does_not_load_legacy_profile():
    workflow = yaml.safe_load((ROOT / ".github/workflows/microcap-realtime-digest.yml").read_text(encoding="utf-8"))
    for job in workflow["jobs"].values():
        env = job.get("env", {})
        assert "MICROCAP_CSV_LINE_TERMINATOR" not in env
        assert "MICROCAP_REVIEWED_NON_ST_BOUNDARY" not in env
        assert "microcap_csv_compat" not in env.get("PYTHONPATH", "")
