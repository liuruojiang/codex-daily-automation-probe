from __future__ import annotations

import argparse
import io
import json
import os
import re
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath


ARTIFACT_NAME = "microcap-verified-state-recovery"
WORKFLOW_PATH = ".github/workflows/microcap-realtime-digest.yml"
STATE_FILE = "microcap-top100-state.zip"
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024


class StripCrossOriginAuthRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow the signed download redirect without forwarding GitHub auth."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        if urllib.parse.urlsplit(req.full_url).netloc.lower() != urllib.parse.urlsplit(newurl).netloc.lower():
            for name in ("Authorization", "X-GitHub-Api-Version", "Accept"):
                redirected.remove_header(name)
        return redirected


def api_request(url: str, token: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "microcap-state-restore",
        },
    )


def eligible_run(run: object, run_id: int) -> bool:
    return (
        isinstance(run, dict)
        and run.get("id") == run_id
        and run.get("status") == "completed"
        # A cancellation after the state artifact upload is still usable: the
        # artifact itself is subsequently revalidated by realtime_state_bundle.
        and run.get("conclusion") in {"success", "cancelled"}
        and run.get("head_branch") == "main"
        and str(run.get("path", "")).split("@", 1)[0] == WORKFLOW_PATH
    )


def fetch_latest(repository: str, token: str, api_url: str, *,
                 artifact_name: str = ARTIFACT_NAME, require_success: bool = False) -> dict[str, object] | None:
    if artifact_name != ARTIFACT_NAME and not re.fullmatch(r"microcap-verified-state-recovery-v2-[0-9a-f]{40}", artifact_name):
        raise ValueError("Recovery requires a formal artifact name bound to the exact strategy SHA")
    base = f"{api_url.rstrip('/')}/repos/{repository}/actions"
    name = urllib.parse.quote(artifact_name, safe="")
    artifacts: list[dict[str, object]] = []
    for page in range(1, 101):
        with urllib.request.urlopen(api_request(f"{base}/artifacts?name={name}&per_page=100&page={page}", token), timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        items = payload.get("artifacts") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise RuntimeError("GitHub artifacts response must contain an artifacts list")
        artifacts.extend(item for item in items if isinstance(item, dict))
        if len(items) < 100:
            break
    else:
        raise RuntimeError("GitHub artifact pagination exceeded safety limit")

    candidates = sorted(
        (
            item for item in artifacts
            if item.get("name") == artifact_name and item.get("expired") is not True and item.get("archive_download_url")
        ),
        key=lambda item: (str(item.get("created_at", "")), int(item.get("id", 0))),
        reverse=True,
    )
    for artifact in candidates:
        run_id = (artifact.get("workflow_run") or {}).get("id")
        if not isinstance(run_id, int) or run_id <= 0:
            continue
        with urllib.request.urlopen(api_request(f"{base}/runs/{run_id}", token), timeout=30) as response:
            run = json.loads(response.read().decode("utf-8"))
        if not eligible_run(run, run_id) or (require_success and run.get("conclusion") != "success"):
            continue
        if artifact["archive_download_url"] != f"{base}/artifacts/{artifact['id']}/zip":
            raise RuntimeError("state archive download URL does not match trusted repository")
        return artifact
    return None


def download(artifact: dict[str, object], token: str) -> bytes:
    opener = urllib.request.build_opener(StripCrossOriginAuthRedirectHandler())
    with opener.open(api_request(str(artifact["archive_download_url"]), token), timeout=60) as response:
        data = response.read(MAX_ARCHIVE_BYTES + 1)
    if len(data) > MAX_ARCHIVE_BYTES:
        raise RuntimeError("state artifact exceeds safety limit")
    return data


def extract(data: bytes, destination: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = [item for item in archive.infolist() if not item.is_dir()]
        if len(members) != 1:
            raise RuntimeError("state artifact must contain exactly one file")
        member = members[0]
        path = PurePosixPath(member.filename)
        if member.flag_bits & 0x1 or path != PurePosixPath(STATE_FILE) or "\\" in member.filename:
            raise RuntimeError("state artifact has an unsafe or unexpected member")
        if member.file_size > MAX_ARCHIVE_BYTES:
            raise RuntimeError("state bundle exceeds safety limit")
        content = archive.read(member)  # reads and CRC-checks before writing
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)


def write_output(restored: bool, artifact_id: str = "") -> None:
    rendered = f"restored={str(restored).lower()}\nartifact_id={artifact_id}\n"
    output = os.environ.get("GITHUB_OUTPUT", "").strip()
    if output:
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(rendered)
    else:
        print(rendered, end="")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--artifact-name", default=ARTIFACT_NAME)
    parser.add_argument("--require-success", action="store_true")
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    parser.add_argument("--api-url", default=os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    args = parser.parse_args()
    if not args.repository or not args.token:
        raise SystemExit("GITHUB_REPOSITORY and GITHUB_TOKEN are required")
    artifact = fetch_latest(args.repository, args.token, args.api_url, artifact_name=args.artifact_name, require_success=args.require_success)
    if artifact is None:
        write_output(False)
        return 0
    extract(download(artifact, args.token), Path(args.bundle))
    write_output(True, str(artifact.get("id", "")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
