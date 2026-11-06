"""Recover only a published small member checkpoint; never fall back to a whole state."""
from __future__ import annotations
import argparse
import io
import hashlib
import json
import os
import re
import stat
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from restore_microcap_verified_state import api_request, download
from restore_microcap_verified_state import StripCrossOriginAuthRedirectHandler

WORKFLOWS = {".github/workflows/microcap-member-daily.yml", ".github/workflows/microcap-member-bootstrap.yml",
             ".github/workflows/microcap-member-maintenance.yml"}
MAX_BYTES = 64 * 1024 * 1024


def unpack(raw: bytes, out: Path):
    if len(raw) > MAX_BYTES:
        raise ValueError("Member checkpoint archive exceeds small-package budget")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        if len(infos) > 32 or sum(x.file_size for x in infos) > MAX_BYTES:
            raise ValueError("Member checkpoint payload exceeds budget")
        seen = set()
        for item in infos:
            name = PurePosixPath(item.filename)
            if (name.is_absolute() or ".." in name.parts or "\\" in item.filename or ":" in item.filename
                    or any(part.endswith(('.', ' ')) for part in name.parts)
                    or item.filename.casefold() in seen or stat.S_ISLNK(item.external_attr >> 16)):
                raise ValueError("Unsafe member checkpoint archive path")
            seen.add(item.filename.casefold())
        if "checkpoint.json" not in seen:
            raise ValueError("Member checkpoint root is missing")
        out.mkdir(parents=True, exist_ok=False)
        for item in infos:
            target = out.joinpath(*PurePosixPath(item.filename).parts)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as stream:
                    stream.write(archive.read(item))


def fetch_latest(repository: str, token: str, sha: str):
    if re.fullmatch(r"[0-9a-f]{40}", sha) is None:
        raise ValueError("Member checkpoint requires exact strategy SHA")
    base = f"https://api.github.com/repos/{repository}/actions"
    name = "microcap-member-checkpoint-v1-" + sha
    candidates = []
    for page in range(1, 101):
        url = f"{base}/artifacts?name={urllib.parse.quote(name)}&per_page=100&page={page}"
        with urllib.request.urlopen(api_request(url, token), timeout=30) as response:
            items = json.load(response)["artifacts"]
        candidates.extend(x for x in items if x.get("name") == name and not x.get("expired"))
        if len(items) < 100:
            break
    else:
        raise RuntimeError("Member artifact pagination exceeded safety limit")
    for artifact in sorted(candidates, key=lambda x: int(x["id"]), reverse=True):
        run_id = artifact["workflow_run"]["id"]
        with urllib.request.urlopen(api_request(f"{base}/runs/{run_id}", token), timeout=30) as response:
            run = json.load(response)
        if (run.get("head_branch") != "main" or run.get("status") != "completed"
                or run.get("path", "").split("@", 1)[0] not in WORKFLOWS):
            continue
        with urllib.request.urlopen(api_request(f"{base}/runs/{run_id}/jobs?per_page=100", token), timeout=30) as response:
            jobs = json.load(response)["jobs"]
        verified_upload = any(any(s.get("name") == "Publish verified member checkpoint" and s.get("conclusion") == "success"
                                 for s in j.get("steps", [])) for j in jobs)
        if not verified_upload:
            continue
        if artifact.get("archive_download_url") != f"{base}/artifacts/{artifact['id']}/zip":
            raise ValueError("Unexpected member checkpoint download URL")
        return artifact
    raise RuntimeError("No published member checkpoint: run the explicit one-time bootstrap; whole-state fallback is disabled")


def seed_download(path: Path, sha: str) -> bytes:
    config = json.loads(path.read_bytes())
    url = config.get("url", "")
    expected = config.get("sha256", "")
    if (config.get("schema") != "member_daily_seed_v1" or config.get("strategy_sha") != sha
            or not url.startswith("https://github.com/liuruojiang/microcap/releases/download/")
            or re.fullmatch(r"[0-9a-f]{64}", expected) is None):
        raise ValueError("Member seed config identity is not approved")
    opener = urllib.request.build_opener(StripCrossOriginAuthRedirectHandler())
    with opener.open(url, timeout=60) as response:
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("Member seed archive size/SHA mismatch")
    return raw


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--strategy-sha", required=True)
    p.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    p.add_argument("--seed-config", type=Path)
    args = p.parse_args()
    token = os.environ["GITHUB_TOKEN"]
    try:
        artifact = fetch_latest(args.repository, token, args.strategy_sha)
    except RuntimeError as exc:
        if not str(exc).startswith("No published member checkpoint:") or args.seed_config is None:
            raise
        unpack(seed_download(args.seed_config, args.strategy_sha), args.out)
        print(json.dumps({"source": "exact_small_bootstrap_seed", "strategy_sha": args.strategy_sha}))
    else:
        unpack(download(artifact, token), args.out)
        print(json.dumps({"artifact_id": artifact["id"], "run_id": artifact["workflow_run"]["id"], "strategy_sha": args.strategy_sha}))


if __name__ == "__main__":
    main()
