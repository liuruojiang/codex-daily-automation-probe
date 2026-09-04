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


def delivery_date(value: datetime) -> date:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(BJ).date()


REVISION = "r7"


def marker_prefix(value: date, publication_mode: str) -> str:
    mode = publication_mode.strip().lower()
    if mode not in {"realtime", "close_confirmed"}:
        raise ValueError(f"unsupported publication mode: {publication_mode}")
    return f"ic-im-v1-3-{REVISION}-{mode}-digest-delivered-{value.isoformat()}-"


def marker_exists(payload: dict[str, object], prefix: str) -> bool:
    artifacts = payload.get("artifacts", [])
    return isinstance(artifacts, list) and any(
        isinstance(item, dict)
        and str(item.get("name", "")).startswith(prefix)
        and item.get("expired") is not True
        for item in artifacts
    )


def fetch_artifacts(repository: str, token: str, api_url: str) -> dict[str, object]:
    url = f"{api_url.rstrip('/')}/repos/{repository}/actions/artifacts?per_page=100"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ic-im-delivery-gate",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("GitHub artifacts response must be an object")
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
    parser.add_argument(
        "--publication-mode",
        choices=("realtime", "close_confirmed"),
        default="realtime",
    )
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    parser.add_argument("--api-url", default=os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    args = parser.parse_args()

    day = delivery_date(now_utc())
    prefix = marker_prefix(day, args.publication_mode)
    exists = False
    if not args.correction:
        if not args.repository or not args.token:
            raise SystemExit("GITHUB_REPOSITORY and GITHUB_TOKEN are required")
        exists = marker_exists(
            fetch_artifacts(args.repository, args.token, args.api_url), prefix
        )
    write_outputs(
        {
            "should_send": str(args.correction or not exists).lower(),
            "marker_prefix": prefix,
            "subject_prefix": "纠正版" if args.correction else "",
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
