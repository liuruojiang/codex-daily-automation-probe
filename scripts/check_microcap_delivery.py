from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


BJ = ZoneInfo("Asia/Shanghai")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def beijing_delivery_date(value: datetime) -> date:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(BJ).date()


def delivery_marker_name(value: date) -> str:
    return f"microcap-realtime-digest-delivered-{value.isoformat()}"


def marker_exists(payload: dict[str, object], marker_name: str) -> bool:
    artifacts = payload.get("artifacts", [])
    if not isinstance(artifacts, list):
        return False
    return any(
        isinstance(item, dict)
        and item.get("name") == marker_name
        and item.get("expired") is not True
        for item in artifacts
    )


def should_send(*, correction: bool, marker_already_exists: bool) -> bool:
    return correction or not marker_already_exists


def fetch_artifacts(
    repository: str,
    token: str,
    marker_name: str,
    api_url: str = "https://api.github.com",
) -> dict[str, object]:
    quoted_name = urllib.parse.quote(marker_name, safe="")
    url = f"{api_url.rstrip('/')}/repos/{repository}/actions/artifacts?name={quoted_name}&per_page=100"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "microcap-delivery-gate",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("GitHub artifacts response must be a JSON object")
    return payload


def write_outputs(values: dict[str, str]) -> None:
    rendered = "".join(f"{key}={value}\n" for key, value in values.items())
    output_path = os.environ.get("GITHUB_OUTPUT", "").strip()
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as handle:
            handle.write(rendered)
    else:
        print(rendered, end="")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--correction", action="store_true")
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    parser.add_argument("--api-url", default=os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    args = parser.parse_args()

    delivery_date = beijing_delivery_date(now_utc())
    marker_name = delivery_marker_name(delivery_date)
    marker_already_exists = False
    if not args.correction:
        if not args.repository or not args.token:
            raise SystemExit("GITHUB_REPOSITORY and GITHUB_TOKEN are required for scheduled delivery checks")
        payload = fetch_artifacts(args.repository, args.token, marker_name, args.api_url)
        marker_already_exists = marker_exists(payload, marker_name)

    send = should_send(
        correction=args.correction,
        marker_already_exists=marker_already_exists,
    )
    write_outputs(
        {
            "should_send": str(send).lower(),
            "delivery_date": delivery_date.isoformat(),
            "marker_name": marker_name,
            "subject_prefix": "纠正版" if args.correction else "",
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
