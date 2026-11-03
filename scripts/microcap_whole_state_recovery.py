"""Recover the exact whole-delivery format produced by the close workflow.

Archive recovery establishes an older verified seed, never today's freshness.
Diagnostic/validation artifacts are deliberately excluded from production discovery.
"""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import zipfile

from restore_microcap_verified_state import api_request, eligible_run

PREFIX = "microcap-whole-delivery-recovery-v1-"
BUNDLE_NAME = "microcap-whole-delivery-state.zip"
MAX_BYTES = 2 * 1024 * 1024 * 1024


def artifact_name(strategy_sha: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", strategy_sha):
        raise ValueError("Recovery requires an exact strategy SHA")
    return PREFIX + strategy_sha


def fetch_latest(repository: str, token: str, strategy_sha: str,
                 api_url: str = "https://api.github.com") -> dict | None:
    base = f"{api_url.rstrip('/')}/repos/{repository}/actions"
    name = artifact_name(strategy_sha)
    candidates = []
    for page in range(1, 101):
        url = f"{base}/artifacts?name={urllib.parse.quote(name)}&per_page=100&page={page}"
        with urllib.request.urlopen(api_request(url, token), timeout=30) as response:
            items = json.load(response)["artifacts"]
        candidates.extend(item for item in items
                          if item.get("name") == name and item.get("expired") is False)
        if len(items) < 100:
            break
    else:
        raise RuntimeError("Recovery artifact pagination exceeded safety limit")
    for item in sorted(candidates, key=lambda a: (a["created_at"], a["id"]), reverse=True):
        run_id = (item.get("workflow_run") or {}).get("id")
        if not isinstance(run_id, int) or run_id <= 0:
            continue
        with urllib.request.urlopen(api_request(f"{base}/runs/{run_id}", token), timeout=30) as response:
            run = json.load(response)
        if not eligible_run(run, run_id) or run.get("conclusion") != "success":
            continue
        if item.get("archive_download_url") != f"{base}/artifacts/{item['id']}/zip":
            raise ValueError("Recovery artifact URL does not match trusted repository")
        if not 0 < int(item["size_in_bytes"]) <= MAX_BYTES:
            raise ValueError("Recovery artifact exceeds size limit")
        return item
    return None


def bundle_identity(bundle: Path, strategy_sha: str, not_after: str) -> dict:
    artifact_name(strategy_sha)
    limit = date.fromisoformat(not_after)
    if not 0 < bundle.stat().st_size <= MAX_BYTES:
        raise ValueError("Recovery bundle exceeds size limit")
    with zipfile.ZipFile(bundle) as archive:
        manifest = json.loads(archive.read("outputs/top100_delivery_manifest.json"))
    seed = date.fromisoformat(manifest["expected_date"])
    if (manifest.get("status") != "complete" or manifest.get("ok") is not True
            or manifest.get("scope") != "whole_workspace_delivery"
            or manifest.get("release_sha") != strategy_sha):
        raise ValueError("Recovery bundle lacks complete exact-strategy identity")
    if seed > limit:
        raise ValueError("Recovery seed is newer than the independent target session")
    with bundle.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"strategy_sha": strategy_sha, "seed_date": seed.isoformat(),
            "bundle_sha256": digest, "bundle_bytes": bundle.stat().st_size,
            "freshness": "verified_archive_only_requires_current_refresh"}


def restore_bundle(root: Path, bundle: Path, strategy_sha: str, not_after: str) -> dict:
    identity = bundle_identity(bundle, strategy_sha, not_after)
    # Invoke the existing complete restore: archive hashes, metadata, historical
    # semantics, backups and independent three-version final-CSV readback remain.
    subprocess.run([sys.executable, "-B", "-u", str(root / "scripts/top100_cloud_delivery.py"),
                    "restore", "--root", str(root), "--bundle", str(bundle),
                    "--expected-date", identity["seed_date"]], check=True)
    print("[whole-recovery] official restore and all-three-stream readback passed", flush=True)
    return {**identity, "restored": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("restore", "describe"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--strategy-sha", required=True)
    parser.add_argument("--not-after-date", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--require-hit", action="store_true")
    args = parser.parse_args()
    root, bundle = args.root.resolve(), args.bundle.resolve()
    actual_sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    if actual_sha != args.strategy_sha:
        raise ValueError("Recovery strategy SHA differs from actual checkout")
    artifact_name(args.strategy_sha)
    if args.command == "describe":
        report = bundle_identity(bundle, args.strategy_sha, args.not_after_date)
    else:
        token = os.environ.get("GITHUB_TOKEN", "")
        repository = os.environ.get("GITHUB_REPOSITORY", "")
        if not token or not repository:
            raise RuntimeError("Recovery requires authenticated repository context")
        artifact = fetch_latest(repository, token, args.strategy_sha)
        report = {"restored": False, "strategy_sha": args.strategy_sha}
        if artifact is None:
            print("[whole-recovery] no successful exact-strategy whole-delivery artifact", flush=True)
            if args.require_hit:
                raise RuntimeError("Required next-run recovery artifact was not found")
        else:
            # Reuse the strategy's existing bounded, streaming/resumable transport.
            sys.path.insert(0, str(root))
            from scripts import top100_cloud_delivery as cloud
            print(f"[whole-recovery] hit artifact={artifact['id']} run={artifact['workflow_run']['id']}", flush=True)
            bundle.parent.mkdir(parents=True, exist_ok=True)
            downloaded, transport = cloud._download_artifact(artifact, bundle.parent)
            digest = artifact.get("digest", "")
            if digest and digest != "sha256:" + transport["github_artifact_sha256"]:
                raise ValueError("GitHub artifact SHA-256 mismatch")
            if downloaded.resolve() != bundle:
                os.replace(downloaded, bundle)
            report = {**restore_bundle(root, bundle, args.strategy_sha, args.not_after_date),
                      **transport, "source_run": artifact["workflow_run"]["id"],
                      "artifact_name": artifact["name"]}
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report), flush=True)
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as handle:
            for key in ("restored", "seed_date", "bundle_sha256", "source_run", "github_artifact"):
                if key in report:
                    value = str(report[key]).lower() if isinstance(report[key], bool) else str(report[key])
                    handle.write(f"{key}={value}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
