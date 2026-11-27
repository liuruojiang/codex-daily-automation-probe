from datetime import date, datetime
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import resolve_ic_im_v1_4_scheduled_slot as slot


def at(value):
    return datetime.fromisoformat(value)


@pytest.mark.parametrize("created", [
    "2026-11-26T12:00:03+00:00",
    # Actual delayed Actions fallback 45892686149 reached the runner at 04:40.
    "2026-11-26T20:40:04+00:00",
])
def test_scheduled_slot_is_prior_close_even_after_midnight(created):
    result = slot.plan_slot(event="schedule", cron=slot.BACKUP_CRON, external=False,
                            key="", created_at=at(created), now=at("2026-11-27T07:00+08:00"))
    assert result == {"route": "scheduled", "target_date": "2026-11-26",
                      "collection_window_open": "true"}


def test_retry_later_keeps_github_creation_slot_and_closes_market_collection_window():
    result = slot.plan_slot(event="schedule", cron=slot.BACKUP_CRON, external=False,
                            key="", created_at=at("2026-11-27T04:40+08:00"),
                            now=at("2026-11-27T20:03+08:00"))
    assert result["target_date"] == "2026-11-26"
    assert result["collection_window_open"] == "false"


@pytest.mark.parametrize(("created", "expected_day", "route"), [
    ("2026-11-28T05:00+08:00", "2026-11-27", "scheduled"),
    ("2026-11-28T20:00+08:00", "2026-11-28", "non_trading_slot"),
    ("2026-10-03T04:00+08:00", "2026-10-02", "non_trading_slot"),
    ("2027-01-01T21:00+08:00", "2027-01-01", "non_trading_slot"),
])
def test_calendar_uses_identified_session_not_runner_weekday(created, expected_day, route):
    result = slot.plan_slot(event="schedule", cron=slot.BACKUP_CRON, external=False,
                            key="", created_at=at(created), now=at(created))
    assert result["target_date"] == expected_day
    assert result["route"] == route


def test_external_slot_binds_the_original_day_and_legacy_early_dispatch_stays_blocked():
    early = at("2026-11-27T04:40+08:00")
    assert slot.plan_slot(event="workflow_dispatch", cron="", external=True,
                          key="", created_at=early, now=early)["route"] == "legacy_external_not_due"
    keyed = slot.plan_slot(event="workflow_dispatch", cron="", external=True,
                          key="icim-close-2026-11-26", created_at=early, now=early)
    assert keyed["target_date"] == "2026-11-26"
    assert keyed["collection_window_open"] == "true"
    with pytest.raises(ValueError, match="elapsed"):
        slot.plan_slot(event="workflow_dispatch", cron="", external=True,
                       key="icim-close-2026-11-27", created_at=early, now=early)


def test_manual_routes_need_no_scheduled_metadata_and_cannot_supply_a_slot_key():
    manual = dict(event="workflow_dispatch", cron="", external=False, created_at=None,
                  now=at("2026-11-27T04:40+08:00"))
    assert slot.plan_slot(key="", **manual)["route"] == "manual"
    with pytest.raises(ValueError, match="external schedule"):
        slot.plan_slot(key="icim-close-2026-11-26", **manual)


@pytest.mark.parametrize("identity_mismatch", [None, "id", "event", "head_branch", "path", "head_sha", "repository"])
def test_run_creation_time_is_authenticated(monkeypatch, identity_mismatch):
    repository = "owner/repo"
    run = {"id": 45892686149, "event": "schedule", "head_branch": "main", "path": slot.WORKFLOW,
           "head_sha": "a" * 40, "repository": {"full_name": repository}, "created_at": "2026-11-26T20:40:04Z"}
    if identity_mismatch:
        run[identity_mismatch] = None
    monkeypatch.setattr(slot.urllib.request, "urlopen",
                        lambda request, timeout: io.BytesIO(json.dumps(run).encode()))
    args = dict(repository=repository, token="isolated-token", api_url="https://api.github.invalid",
                run_id="45892686149", event="schedule", head_sha="a" * 40)
    if identity_mismatch:
        with pytest.raises(ValueError, match="metadata does not match"):
            slot.read_run_created_at(**args)
    else:
        assert slot.read_run_created_at(**args) == at("2026-11-26T20:40:04+00:00")


def workflow():
    return yaml.safe_load((ROOT / slot.WORKFLOW).read_text(encoding="utf-8"))["jobs"]


@pytest.mark.parametrize("variant", ["complete", "only-intent", "only-marker", "foreign-ledger", "expired"])
def test_lightweight_lookup_requires_both_delivery_and_ledger_evidence_from_same_run(monkeypatch, variant):
    repository, run_id, head, day = "owner/repo", 42, "a" * 40, date(2026, 11, 26)
    run = dict(id=run_id, event="workflow_dispatch", head_branch="main", path=slot.WORKFLOW,
               head_sha=head, repository={"full_name": repository}, status="completed", conclusion="success")
    origin = dict(id=run_id, head_sha=head)
    artifacts = [
        dict(id=61, name=slot.marker_prefix(day, "close_confirmed") + "d" * 12,
             workflow_run=origin, digest="sha256:" + "a" * 64, expired=variant == "expired"),
        dict(id=62, name=slot.LEDGER_NAME, workflow_run=dict(origin, id=43) if variant == "foreign-ledger" else origin,
             digest="sha256:" + "b" * 64, expired=False),
    ]
    if variant == "only-intent":
        artifacts[0]["name"] += "-send-intent"
    if variant == "only-marker":
        del artifacts[1]
    requested = []

    def read(url, _token):
        requested.append(url)
        return {"workflow_runs": [run]} if len(requested) == 1 else {"artifacts": artifacts}

    monkeypatch.setattr(slot, "read_actions_json", read)
    found = slot.published_delivery_for_slot(repository, "test-placeholder", "https://api.github.invalid", day)
    assert len(requested) == 2
    assert "2026-11-26T04%3A00%3A00Z" in requested[0]
    if variant == "complete":
        assert found == {"normal_marker_id": "61", "ledger_artifact_id": "62", "delivered_run_id": "42"}
    else:
        assert found == {}


@pytest.mark.parametrize(("route", "window", "should_send", "status", "result"), [
    ("scheduled", "true", "true", "due", "true"),
    ("scheduled", "true", "false", "delivered", "false"),
    ("scheduled", "true", "false", "recover_marker", "false"),
    ("scheduled", "false", "false", "delivered", "false"),
    ("scheduled", "false", "true", "due", None),
    ("scheduled", "true", "", "", None),
    ("non_trading_slot", "false", "", "", "false"),
    ("legacy_external_not_due", "false", "", "", "false"),
    ("manual", "false", "", "", "true"),
])
def test_original_day_gate_controls_actual_preflight_and_required_regression(
        tmp_path, route, window, should_send, status, result):
    jobs = workflow()
    assert jobs["regression"]["needs"] == "preflight"
    assert jobs["regression"]["if"] == "needs.preflight.outputs.run_required == 'true'"
    assert jobs["send"]["needs"] == ["preflight", "regression"]
    bash = shutil.which("bash")
    if os.name == "nt":
        git = shutil.which("git")
        git_bash = Path(git).parents[1] / "bin" / "bash.exe"
        if git_bash.is_file():
            bash = str(git_bash)
    assert bash
    steps = jobs["preflight"]["steps"]
    gate = next(step for step in steps if step.get("id") == "original_day_gate")
    assert '--delivery-date "$TARGET_DATE"' in gate["run"]
    plan = next(step for step in steps if step.get("id") == "plan")
    plan_path = tmp_path / "preflight.sh"
    plan_path.write_text(plan["run"], encoding="utf-8", newline="\n")
    env = dict(os.environ, ROUTE=route, TARGET_DATE="2026-11-26", WINDOW_OPEN=window,
               SHOULD_SEND=should_send, DELIVERY_STATUS=status, DELIVERED_RUN="", MARKER_ID="", LEDGER_ID="",
               GITHUB_OUTPUT=str(tmp_path / "output"), GITHUB_STEP_SUMMARY=str(tmp_path / "summary"))
    run = subprocess.run([bash, "-eo", "pipefail", str(plan_path)], env=env, capture_output=True, text=True)
    if result is None:
        assert run.returncode == 1
        assert "Unresolved ICIM delivery for 2026-11-26" in run.stdout
    else:
        assert run.returncode == 0, run.stderr
        assert (tmp_path / "output").read_text().strip() == f"run_required={result}"


def test_same_bound_date_feeds_native_strategy_and_valuation_consumer():
    steps = {step.get("id"): step for step in workflow()["send"]["steps"] if step.get("id")}
    assert steps["calendar"]["env"]["TARGET_DATE"] == "${{ needs.preflight.outputs.target_date }}"
    assert "selected != completed" in steps["calendar"]["run"]
    run = steps["run_signal"]
    assert run["env"]["TARGET_DAY"] == "${{ steps.calendar.outputs.target_day }}"
    assert '--expected-date "$TARGET_DAY"' in run["run"]
    assert '--expected-market-date "$TARGET_DAY"' in run["run"]
    assert "--now" not in run["run"]
