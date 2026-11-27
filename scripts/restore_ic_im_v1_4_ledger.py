from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath


MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_FILES = 500
ARTIFACT_NAME = "ic-im-v1-4-r1-ledger"
WORKFLOW = ".github/workflows/ic-im-v1-4-daily-digest.yml"
LEDGER_STEPS = (
    "Verify v1.4 ledger chain and migration proof before runner import",
    "Run IC/IM signal",
    "Build Gmail digest",
    "Prepare delivery marker",
    "Recheck delivery against actual signal day",
    "Upload successful ledger",
)
SEND_STEPS = (
    "Persist normal-send intent before SMTP",
    "Send Gmail",
    "Preserve accepted SMTP receipt",
    "Mark digest delivered",
)


class StripCrossOriginAuthRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow artifact redirects without leaking GitHub auth cross-origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        old_origin = urllib.parse.urlsplit(req.full_url).netloc.lower()
        new_origin = urllib.parse.urlsplit(newurl).netloc.lower()
        if old_origin != new_origin:
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
            "User-Agent": "ic-im-ledger-restore",
        },
    )


def latest_artifact(
    payload: dict[str, object],
    name: str = ARTIFACT_NAME,
    before_created_at: str = "",
) -> dict[str, object] | None:
    items = payload.get("artifacts", [])
    if not isinstance(items, list):
        return None
    candidates = [
        item
        for item in items
        if isinstance(item, dict)
        and item.get("name") == name
        and item.get("expired") is not True
        and item.get("archive_download_url")
        and (not before_created_at or str(item.get("created_at", "")) < before_created_at)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: (str(item.get("created_at", "")), int(item.get("id", 0))))


def _github_second(value):
    if not isinstance(value, str):
        raise ValueError("GitHub timestamp must be a timezone-aware string")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.utcoffset() is None:
        raise ValueError("GitHub timestamp lacks timezone")
    return stamp.replace(microsecond=0)


def _failed_run_artifact_matches(run, artifact, repository, api_url):
    if not isinstance(run, dict) or not isinstance(artifact, dict):
        return False
    run_id, attempt, head = run.get("id"), run.get("run_attempt"), run.get("head_sha")
    origin = artifact.get("workflow_run")
    artifact_id, size, digest = artifact.get("id"), artifact.get("size_in_bytes"), artifact.get("digest")
    base = f"{api_url.rstrip('/')}/repos/{repository}/actions"
    repo, head_repo = run.get("repository"), run.get("head_repository")
    if (type(run_id) is not int or run_id <= 0 or type(attempt) is not int or attempt <= 0
            or not isinstance(head, str) or re.fullmatch("[0-9a-f]{40}", head) is None
            or run.get("head_branch") != "main" or run.get("status") != "completed"
            or run.get("conclusion") != "failure" or run.get("event") not in {"schedule", "workflow_dispatch"}
            or str(run.get("path", "")).split("@", 1)[0] != WORKFLOW
            or not isinstance(repo, dict) or repo.get("full_name") != repository
            or not isinstance(head_repo, dict) or head_repo.get("full_name") != repository
            or not isinstance(origin, dict) or origin.get("id") != run_id or origin.get("head_sha") != head
            or origin.get("head_branch") != "main" or artifact.get("name") != ARTIFACT_NAME
            or artifact.get("expired") is not False or type(artifact_id) is not int or artifact_id <= 0
            or type(size) is not int or not 0 < size <= MAX_ARCHIVE_BYTES
            or not isinstance(digest, str) or re.fullmatch("sha256:[0-9a-f]{64}", digest) is None
            or artifact.get("archive_download_url") != f"{base}/artifacts/{artifact_id}/zip"):
        return False
    return True


def verify_ledger_publication(run, artifact, jobs, *, repository, api_url="https://api.github.com"):
    """Prove a completed ledger upload from an attempt which failed in mail delivery."""
    if not _failed_run_artifact_matches(run, artifact, repository, api_url) or not isinstance(jobs, dict):
        return None
    run_id, attempt, head = run["id"], run["run_attempt"], run["head_sha"]
    entries = jobs.get("jobs")
    total = jobs.get("total_count")
    if (not isinstance(entries, list) or type(total) is not int or len(entries) != total or total > 100):
        return None
    selected = [job for job in entries if isinstance(job, dict) and job.get("name") == "send"
                and job.get("run_id") == run_id and type(job.get("run_attempt")) is int
                and job["run_attempt"] == attempt]
    if len(selected) != 1:
        return None
    job = selected[0]
    job_id = job.get("id")
    if (type(job_id) is not int or job_id <= 0 or job.get("head_sha") != head
            or job.get("status") != "completed" or job.get("conclusion") != "failure"
            or not isinstance(job.get("steps"), list)):
        return None
    pipeline = LEDGER_STEPS + SEND_STEPS
    steps = [step for step in job["steps"] if isinstance(step, dict) and step.get("name") in pipeline]
    if len(steps) != len(pipeline) or {step["name"] for step in steps} != set(pipeline):
        return None
    by_name = {step["name"]: step for step in steps}
    positions = [by_name[name].get("number") for name in pipeline]
    if (any(type(position) is not int or position <= 0 for position in positions)
            or positions != sorted(set(positions))):
        return None
    if any(by_name[name].get("status") != "completed" or by_name[name].get("conclusion") != "success"
           for name in LEDGER_STEPS):
        return None
    if not any(by_name[name].get("status") == "completed"
               and by_name[name].get("conclusion") == "failure" for name in SEND_STEPS):
        return None
    upload = by_name["Upload successful ledger"]
    try:
        created = _github_second(artifact.get("created_at"))
        if not _github_second(upload.get("started_at")) <= created <= _github_second(upload.get("completed_at")):
            return None
    except ValueError:
        return None
    return {"schema": "icim_published_ledger_stage_v1", "run_id": run_id, "run_attempt": attempt,
            "job_id": job_id, "head_sha": head, "artifact_id": artifact["id"],
            "size_in_bytes": artifact["size_in_bytes"], "digest": artifact["digest"],
            "upload_started_at": upload["started_at"],
            "upload_completed_at": upload["completed_at"], "artifact_created_at": artifact["created_at"]}


def fetch_latest(
    repository: str,
    token: str,
    api_url: str,
    artifact_name: str = ARTIFACT_NAME,
    before_created_at: str = "",
) -> dict[str, object] | None:
    name = urllib.parse.quote(artifact_name, safe="")
    base = f"{api_url.rstrip('/')}/repos/{repository}/actions"
    candidates = []
    for page in range(1, 101):
        url = f"{base}/artifacts?name={name}&per_page=100&page={page}"
        with urllib.request.urlopen(api_request(url, token), timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("artifacts"), list):
            raise RuntimeError("GitHub artifacts response must contain an artifacts list")
        candidates.extend(payload["artifacts"])
        if len(payload["artifacts"]) < 100:
            break
    else:
        raise RuntimeError("GitHub artifact pagination exceeded safety limit")
    while (artifact := latest_artifact(
        {"artifacts": candidates}, artifact_name, before_created_at
    )) is not None:
        candidates.remove(artifact)
        run_id = (artifact.get("workflow_run") or {}).get("id")
        if not isinstance(run_id, int) or run_id <= 0:
            continue
        with urllib.request.urlopen(api_request(f"{base}/runs/{run_id}", token), timeout=30) as response:
            run = json.loads(response.read().decode("utf-8"))
        if (isinstance(run, dict) and run.get("id") == run_id
                and run.get("status") == "completed" and run.get("conclusion") == "success"
                and run.get("head_branch") == "main"
                and str(run.get("path", "")).split("@", 1)[0] == WORKFLOW):
            if artifact["archive_download_url"] != f"{base}/artifacts/{artifact['id']}/zip":
                raise RuntimeError("ledger archive download URL does not match trusted repository")
            return artifact
        if artifact_name == ARTIFACT_NAME and _failed_run_artifact_matches(run, artifact, repository, api_url):
            jobs_url = f"{base}/runs/{run_id}/jobs?filter=all&per_page=100"
            with urllib.request.urlopen(api_request(jobs_url, token), timeout=30) as response:
                jobs = json.loads(response.read().decode("utf-8"))
            proof = verify_ledger_publication(run, artifact, jobs, repository=repository, api_url=api_url)
            if proof is not None:
                return dict(artifact, _ledger_publication=proof)
    return None


def download(artifact: dict[str, object], token: str) -> bytes:
    url = str(artifact["archive_download_url"])
    opener = urllib.request.build_opener(StripCrossOriginAuthRedirectHandler())
    with opener.open(api_request(url, token), timeout=60) as response:
        data = response.read(MAX_ARCHIVE_BYTES + 1)
    if len(data) > MAX_ARCHIVE_BYTES:
        raise RuntimeError("ledger artifact exceeds safety limit")
    proof = artifact.get("_ledger_publication")
    if proof and (len(data) != proof["size_in_bytes"]
                  or "sha256:" + hashlib.sha256(data).hexdigest() != proof["digest"]):
        raise RuntimeError("Verified ledger stage download differs from the original artifact")
    return data


def safe_members(data: bytes) -> list[zipfile.ZipInfo]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
    if len(members) > MAX_FILES:
        raise RuntimeError("ledger artifact contains too many files")
    if sum(item.file_size for item in members) > MAX_ARCHIVE_BYTES:
        raise RuntimeError("ledger artifact expands beyond safety limit")
    seen = set()
    for item in members:
        path = PurePosixPath(item.filename)
        if str(path).casefold() in seen:
            raise RuntimeError("duplicate ledger artifact member")
        seen.add(str(path).casefold())
        if item.flag_bits & 0x1:
            raise RuntimeError("encrypted ledger artifact is not allowed")
        if path.is_absolute() or ".." in path.parts or "\\" in item.filename or ":" in item.filename:
            raise RuntimeError("unsafe ledger artifact path")
        if item.is_dir():
            continue
        allowed = path in {
            PurePosixPath("latest.json"),
            PurePosixPath("migration_record.json"),
        } or (
            len(path.parts) == 2
            and path.parts[0] == "journal"
            and path.suffix == ".json"
        )
        if not allowed:
            raise RuntimeError(f"unexpected ledger artifact member: {item.filename}")
    return members


def extract(data: bytes, destination: Path) -> None:
    if destination.exists() and any(destination.iterdir()):
        raise RuntimeError("ledger destination must be empty")
    members = safe_members(data)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = {item.filename for item in members if not item.is_dir()}
        for required in ("latest.json", "migration_record.json"):
            if required not in names:
                raise RuntimeError(f"v1.4 ledger artifact is missing {required}")
        # Read and CRC-check every entry before changing the destination.
        contents = {item.filename: archive.read(item) for item in members if not item.is_dir()}
        destination.mkdir(parents=True, exist_ok=True)
        for item in members:
            if item.is_dir():
                continue
            target = destination.joinpath(*PurePosixPath(item.filename).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(contents[item.filename])
    if not (destination / "latest.json").is_file():
        raise RuntimeError("ledger artifact is missing latest.json")
    if not (destination / "migration_record.json").is_file():
        raise RuntimeError("v1.4 ledger artifact is missing migration_record.json")


def write_output(restored: bool, artifact_id: str = "") -> None:
    rendered = f"restored={str(restored).lower()}\nartifact_id={artifact_id}\n"
    output_path = os.environ.get("GITHUB_OUTPUT", "").strip()
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as handle:
            handle.write(rendered)
    else:
        print(rendered, end="")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    parser.add_argument("--api-url", default=os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    parser.add_argument("--artifact-name", default=ARTIFACT_NAME)
    parser.add_argument("--before-created-at", default="")
    parser.add_argument("--required", action="store_true")
    args = parser.parse_args()
    if not args.repository or not args.token:
        raise SystemExit("GITHUB_REPOSITORY and GITHUB_TOKEN are required")
    artifact = fetch_latest(
        args.repository,
        args.token,
        args.api_url,
        args.artifact_name,
        args.before_created_at,
    )
    if artifact is None:
        write_output(False)
        return 1 if args.required else 0
    extract(download(artifact, args.token), Path(args.state_dir))
    write_output(True, str(artifact.get("id", "")))
    if proof := artifact.get("_ledger_publication"):
        print("Restored verified ledger from a publish step completed before a mail failure: "
              + json.dumps(proof, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
