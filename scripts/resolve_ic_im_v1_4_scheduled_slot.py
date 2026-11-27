"""Resolve the due close from authenticated Actions run metadata."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta, timezone

import ic_im_v1_4_delivery_contract as calendar
from check_ic_im_v1_4_delivery import BJ, marker_prefix, write_outputs
from restore_ic_im_v1_4_ledger import ARTIFACT_NAME as LEDGER_NAME, api_request


BACKUP_CRON = "0 12 * * *"
WORKFLOW = ".github/workflows/ic-im-v1-4-daily-digest.yml"
CLOSE_TIME = time(15)
DELIVERY_TIME = time(20)
DISPATCH_KEY = re.compile(r"icim-close-([0-9]{4}-[0-9]{2}-[0-9]{2})")


def slot_for_run(event, cron, external, key, created_at):
    if event not in {"schedule", "workflow_dispatch"}:
        raise ValueError("Unsupported ICIM trigger")
    if event == "workflow_dispatch" and not external:
        if key:
            raise ValueError("The scheduled slot key requires an external schedule")
        return "manual", None
    if event == "schedule" and (cron != BACKUP_CRON or key or external):
        raise ValueError("Unexpected GitHub ICIM schedule identity")
    if created_at is None or created_at.utcoffset() is None:
        raise ValueError("ICIM scheduled run requires a verified timezone-aware created_at")
    created = created_at.astimezone(BJ)
    before_evening = created.time() < DELIVERY_TIME
    latest_due = created.date() - timedelta(days=int(before_evening))
    if event == "workflow_dispatch":
        if key:
            match = DISPATCH_KEY.fullmatch(key)
            if match is None or date.fromisoformat(match[1]) != latest_due:
                raise ValueError("ICIM dispatch key disagrees with the latest elapsed 20:00 slot at acceptance")
        elif before_evening:
            # The pre-key Worker has no slot date. An old 18:00 route must
            # never be inferred as a request for the previous close.
            return "legacy_external_not_due", None
    # A GitHub schedule event supplies the cron, without its nominal timestamp.
    # It reconciles the most recent elapsed 20:00 slot at run creation. A job
    # queued until the next day must keep that date across retries and setup.
    return "scheduled", latest_due


@calendar.with_calendar_policy()
def scheduled_session_window(payload, now):
    target = date.fromisoformat(payload["market_date"])
    if not calendar.is_session(target):
        return "non_trading_slot", None
    next_close = datetime.combine(calendar.adjacent_session(target), CLOSE_TIME, BJ)
    return "scheduled", now < next_close


def read_run_created_at(*, repository, token, api_url, run_id, event, head_sha):
    if not repository or not token or re.fullmatch(r"[1-9][0-9]*", run_id) is None:
        raise ValueError("ICIM run metadata lookup requires an authenticated repository and run ID")
    url = f"{api_url.rstrip('/')}/repos/{repository}/actions/runs/{run_id}"
    run = read_actions_json(url, token)
    if (not isinstance(run, dict) or run.get("id") != int(run_id) or run.get("event") != event
            or run.get("head_sha") != head_sha or not run_matches(run, repository)):
        raise ValueError("ICIM schedule run metadata does not match this main workflow")
    created = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
    if created.utcoffset() is None:
        raise ValueError("ICIM schedule creation time lacks timezone information")
    return created


def read_actions_json(url, token):
    with urllib.request.urlopen(api_request(url, token), timeout=20) as response:
        raw = response.read(128_001)
    if len(raw) > 128_000:
        raise ValueError("ICIM workflow run metadata exceeds the response budget")
    return json.loads(raw)


def run_matches(run, repository):
    origin = run.get("repository")
    return (run.get("head_branch") == "main" and str(run.get("path", "")).split("@", 1)[0] == WORKFLOW
            and isinstance(origin, dict) and origin.get("full_name") == repository)


def published_delivery_for_slot(repository, token, api_url, target):
    """Short-circuit a positive lookup only. A miss still needs full dedupe."""
    base = f"{api_url.rstrip('/')}/repos/{repository}/actions"
    after = datetime.combine(target, CLOSE_TIME, BJ).astimezone(timezone.utc)
    params = urllib.parse.urlencode(dict(branch="main", status="success", per_page=6,
                                       created=">=" + after.strftime("%Y-%m-%dT%H:%M:%SZ")))
    pattern = re.compile(re.escape(marker_prefix(target, "close_confirmed")) + "[0-9a-f]{12}")
    runs = read_actions_json(f"{base}/workflows/{WORKFLOW.rsplit('/', 1)[1]}/runs?{params}", token)
    for run in runs["workflow_runs"]:
        if not isinstance(run, dict):
            continue
        run_id, head = run["id"], run.get("head_sha", "")
        if (type(run_id) is not int or run_id <= 0 or not run_matches(run, repository)
                or run.get("status") != "completed" or run.get("conclusion") != "success"
                or run.get("event") not in {"schedule", "workflow_dispatch"}
                or not isinstance(head, str) or re.fullmatch("[0-9a-f]{40}", head) is None):
            continue
        artifacts = read_actions_json(f"{base}/runs/{run_id}/artifacts?per_page=100", token)
        object_ids = {}
        for item in artifacts["artifacts"]:
            if not isinstance(item, dict):
                continue
            origin = item.get("workflow_run")
            object_id, name, digest = item.get("id"), item.get("name", ""), item.get("digest", "")
            if (type(object_id) is not int or object_id <= 0 or item.get("expired") is not False
                    or not isinstance(origin, dict) or origin.get("id") != run_id or origin.get("head_sha") != head
                    or not isinstance(name, str) or not isinstance(digest, str)
                    or re.fullmatch("sha256:[0-9a-f]{64}", digest) is None):
                continue
            if pattern.fullmatch(name):
                object_ids["normal_marker_id"] = str(object_id)
            elif name == LEDGER_NAME:
                object_ids["ledger_artifact_id"] = str(object_id)
        if set(object_ids) == {"normal_marker_id", "ledger_artifact_id"}:
            return dict(object_ids, delivered_run_id=str(run_id))
    return {}


def plan_slot(*, event, cron, external, key, created_at, now):
    route, target = slot_for_run(event, cron, external, key, created_at)
    result = {"route": route, "target_date": "", "collection_window_open": "false"}
    if target is not None:
        if now.utcoffset() is None or now < created_at or now < datetime.combine(target, DELIVERY_TIME, BJ):
            raise ValueError("ICIM schedule target has not reached its original delivery time")
        route, within_window = scheduled_session_window({"market_date": target.isoformat()}, now)
        result.update(route=route, target_date=target.isoformat(),
                      collection_window_open=str(within_window is True).lower())
    return result


def main():
    env = os.environ
    event = env.get("GITHUB_EVENT_NAME", "")
    external = env.get("EXTERNAL_SCHEDULE") == "true"
    key = env.get("DISPATCH_KEY", "")
    created = None
    repository, token = env.get("GITHUB_REPOSITORY", ""), env.get("GITHUB_TOKEN", "")
    api_url = env.get("GITHUB_API_URL", "https://api.github.com")
    if event == "schedule" or external:
        if (env.get("REQUESTED_MODE") != "close_confirmed"
                or env.get("CORRECTION") == "true" or env.get("LEDGER_ONLY") == "true"):
            raise ValueError("Scheduled ICIM recovery requires close-confirmed mode and ordinary dedupe")
        created = read_run_created_at(repository=repository, token=token, api_url=api_url,
                                      run_id=env.get("GITHUB_RUN_ID", ""), event=event,
                                      head_sha=env.get("GITHUB_SHA", ""))
    plan = plan_slot(event=event, cron=env.get("SCHEDULE_CRON", ""), external=external,
                     key=key, created_at=created, now=datetime.now(BJ))
    if plan["route"] == "scheduled":
        try:
            plan.update(published_delivery_for_slot(repository, token, api_url, date.fromisoformat(plan["target_date"])))
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise
            print("The recent publication lookup failed. Continue to the complete dedupe check.")
        except (OSError, ValueError, KeyError, TypeError):
            print("The recent publication lookup was inconclusive. Continue to the complete dedupe check.")
    write_outputs(plan)
    print(f"ICIM slot route={plan['route']} target={plan['target_date'] or 'none'} "
          f"collection_window_open={plan['collection_window_open']}")


if __name__ == "__main__":
    main()
