"""Ambiguous final signal files and acceptance-mail inputs fail closed."""
import csv
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

import test_microcap_digest_email_body as fixtures

digest = fixtures.digest
ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ("v2.0", "v2.3", "v2.5")
TEST_ID = "microcap-shape-20261007"


def test_control_unique_header_single_row_is_readable(tmp_path):
    path = tmp_path / "signal.csv"
    path.write_text("date,version\n2026-09-30,2.0\n", encoding="utf-8")
    assert digest.read_last_csv_row(str(path)) == ({"date": "2026-09-30", "version": "2.0"}, "")


@pytest.mark.parametrize("payload", [
    b"date,version\r\r\n2026-09-30,2.0\r\r\n",
    b"\n\ndate,version\n\n2026-09-30,2.0\n\n\n",
    b"date,version\r\n\r\n2026-09-30,2.0\r\n\r\n",
])
def test_empty_records_in_windows_exports_or_blank_lines_are_not_signals(tmp_path, payload):
    path = tmp_path / "signal.csv"
    path.write_bytes(payload)
    assert digest.read_last_csv_row(str(path)) == ({"date": "2026-09-30", "version": "2.0"}, "")


@pytest.mark.parametrize("payload", [
    "date,version\n2026-09-30,2.0\n2026-09-30,2.0\n",
    "date,version\n2026-09-29,2.5\n2026-09-30,2.0\n",
    "date,version,version\n2026-09-30,2.5,2.0\n",
    "date,,version\n2026-09-30,unused,2.0\n",
    "date, version\n2026-09-30,2.0\n",
    "date,version\n2026-09-30,2.0,ignored-extra\n",
    "date,version\n2026-09-30\n",
    "date,version\n2026-09-30,2.0\n,\n",
    'date,version\n2026-09-30,2.0\n""\n',
    "date,version\r\r\n2026-09-30,2.0\r\r\n2026-09-30,2.0\r\r\n",
    'date,version\n2026-09-30,"2.0\n',
])
def test_ambiguous_or_malformed_final_csv_is_not_readable(tmp_path, payload):
    path = tmp_path / "signal.csv"
    path.write_text(payload, encoding="utf-8")
    row, note = digest.read_last_csv_row(str(path))
    assert row == {}
    assert note.startswith("final signal CSV")


def prepare(tmp_path):
    args = ["digest", "--out-dir", str(tmp_path / "mail"), "--publication-mode", "close_confirmed",
            "--delivery-test-id", TEST_ID, "--expected-signal-date", "2026-09-30"]
    paths = {}
    for version in VERSIONS:
        row = fixtures.MicrocapDigestEmailBodyTests().signal_fields(version)
        row.update(date="2026-09-30", signal_timing="close_confirmed", official_close_confirmed_signal="True")
        path = tmp_path / f"{version}.csv"
        fixtures.MicrocapDigestEmailBodyTests().write_csv(path, row)
        result = tmp_path / f"{version}.txt"
        result.write_text("signal\n", encoding="utf-8")
        args += ["--result", f"{version}={result}", "--signal-csv", f"{version}={path}", "--exit-code", f"{version}=0"]
        paths[version] = path
    return args, paths


def run(args):
    with patch.object(sys, "argv", args):
        assert digest.main() == 0
    directory = Path(args[args.index("--out-dir") + 1])
    return json.loads((directory / "metadata.json").read_text(encoding="utf-8"))


def smtp_step_allows(metadata):
    workflow = yaml.safe_load((ROOT / ".github/workflows/microcap-realtime-digest.yml").read_text(encoding="utf-8"))
    step = next(item for item in workflow["jobs"]["send"]["steps"] if item.get("name") == "Send Gmail")
    namespace = {
        "always": lambda: True, "true": True, "false": False,
        "inputs": SimpleNamespace(validation_only=False, delivery_test_id=TEST_ID),
        "steps": SimpleNamespace(
            delivery_gate=SimpleNamespace(outputs=SimpleNamespace(should_send="true")),
            digest=SimpleNamespace(outcome="success", outputs=SimpleNamespace(status=metadata["status"])),
            send_intent=SimpleNamespace(outcome="success"),
            publication_mode=SimpleNamespace(outputs=SimpleNamespace(mode="close_confirmed")),
            whole_delivery=SimpleNamespace(outputs=SimpleNamespace(validated="true"))),
    }
    return eval(step["if"].replace("&&", " and ").replace("||", " or "), {"__builtins__": {}}, namespace)


def test_three_version_control_has_explicit_sample_labels_and_is_sendable(tmp_path):
    args, _ = prepare(tmp_path)
    metadata = run(args)
    assert metadata["status"] == "OK"
    assert metadata["subject"].startswith("[发送验收测试][收盘确认]")
    assert "2026-09-30" in metadata["subject"]
    assert "历史收盘日报样本" in metadata["body"]
    assert "不作为今日交易指令" in metadata["body"]
    assert "## 今日结论" not in metadata["body"]
    assert smtp_step_allows(metadata)


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("fault", ["duplicate", "conflict", "header_duplicate", "extra_cell", "missing_cell", "wrong_revision"])
def test_bad_csv_cannot_activate_actual_workflow_send_step(tmp_path, version, fault):
    args, paths = prepare(tmp_path)
    path = paths[version]
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    if fault == "duplicate":
        rows.append(list(rows[1]))
    elif fault == "conflict":
        old = list(rows[1])
        old[rows[0].index("date")] = "2026-09-29"
        rows.insert(1, old)
    elif fault == "header_duplicate":
        rows[0].append("version")
        rows[1].append(rows[1][rows[0].index("version")])
    elif fault == "extra_cell":
        rows[1].append("ignored-extra")
    elif fault == "missing_cell":
        rows[1].pop()
    elif fault == "wrong_revision":
        rows[1][rows[0].index("strategy_revision")] = "retired_revision"
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(rows)
    metadata = run(args)
    assert metadata["status"] == "FAILED"
    assert not smtp_step_allows(metadata)


@pytest.mark.parametrize("fault", ["duplicate_spec", "missing_spec", "extra_spec", "duplicate_result"])
def test_missing_extra_or_duplicate_version_spec_stops_before_metadata(tmp_path, fault):
    args, paths = prepare(tmp_path)
    if fault == "duplicate_spec":
        args += ["--signal-csv", f"v2.0={paths['v2.0']}"]
    elif fault == "missing_spec":
        index = args.index(f"v2.5={paths['v2.5']}")
        del args[index - 1:index + 1]
    elif fault == "extra_spec":
        args += ["--signal-csv", f"v2.6={paths['v2.0']}"]
    else:
        args += ["--result", f"v2.0={tmp_path / 'v2.0.txt'}"]
    with patch.object(sys, "argv", args), pytest.raises(ValueError):
        digest.main()
    assert not (tmp_path / "mail/metadata.json").exists()


def test_test_mode_cannot_write_formal_recovery_or_caches():
    workflow = yaml.safe_load((ROOT / ".github/workflows/microcap-realtime-digest.yml").read_text(encoding="utf-8"))
    steps = {item.get("name"): item for item in workflow["jobs"]["send"]["steps"]}
    cache_steps = [item for item in steps.values() if item.get("uses", "").startswith("actions/cache/save@")]
    assert len(cache_steps) == 2
    for item in cache_steps:
        assert "inputs.delivery_test_id == ''" in item["if"]
    state_name = steps["Preserve validated state bundle for long-gap recovery"]["with"]["name"]
    assert "inputs.delivery_test_id != '' && 'microcap-verified-state-validation'" in state_name
    whole_name = steps["Preserve verified whole delivery for the next trading day"]["with"]["name"]
    assert "inputs.validation_only == true || inputs.delivery_test_id != ''" in whole_name
    assert "&& 'microcap-whole-delivery-validation-state'" in whole_name
