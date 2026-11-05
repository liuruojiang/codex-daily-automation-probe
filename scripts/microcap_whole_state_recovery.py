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
import shutil
import subprocess
import sys
import tempfile
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


def _source_repack_changes(config_raw: bytes) -> dict:
    changes = json.loads(config_raw).get("source_repack_changes", {})
    paired_sources = {"microcap_top100_mom16_biweekly_live_v2_0.py", "scripts/st_metadata_refresh.py"}
    if (not isinstance(changes, dict) or len(changes) > 2
            or (len(changes) == 2 and set(changes) != paired_sources)):
        raise ValueError("Source repack requires one exact source or the reviewed metadata continuation pair")
    for path, pair in changes.items():
        if path != "microcap_top100_mom16_biweekly_live_v2_0.py" and not re.fullmatch(r"scripts/[A-Za-z0-9_]+\.py", path):
            raise ValueError("Source repack declaration is not an allowed execution source")
        if (not isinstance(pair, list) or len(pair) != 2
                or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in pair)
                or pair[0] == pair[1]):
            raise ValueError("Source repack requires distinct exact old/new SHA-256 values")
    return changes


def _verify_repack_inputs(original: dict, updated: dict, changes: dict) -> None:
    if not isinstance(updated, dict) or original.keys() != updated.keys():
        raise ValueError("Approved repack changed input key set")
    actual = {path for path in original if original[path] != updated[path]}
    if actual != set(changes):
        raise ValueError("Approved repack input changes differ from exact source declaration")
    for path, (old_sha, new_sha) in changes.items():
        if original[path] != old_sha or updated[path] != new_sha:
            raise ValueError("Approved repack source hash differs from declaration")


def repack_approved(root: Path, bundle: Path, strategy_sha: str, not_after: str, config_path: Path) -> dict:
    """After approved bootstrap restore, rebind packaging only via native pack.

    The approved original remains immutable. No refresh, signal or SMTP is run.
    Economic inputs/final streams must be exact; only an explicitly approved,
    exact source transition may differ in the native-pack manifest.
    """
    from restore_approved_microcap_state import load_release_config
    config_raw = config_path.read_bytes()
    config = load_release_config(config_path, strategy_sha)
    source_changes = _source_repack_changes(config_raw)
    actual = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    if actual != strategy_sha:
        raise ValueError("Approved repack strategy SHA differs from actual checkout")
    artifact_name(strategy_sha)
    if not 0 < bundle.stat().st_size <= MAX_BYTES:
        raise ValueError("Approved repack bundle exceeds size limit")
    def digest(path):
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()
    approved_sha = digest(bundle)
    if approved_sha != config["state_sha256"].lower():
        raise ValueError("Approved repack archive SHA-256 differs from config")
    manifest_name = "outputs/top100_delivery_manifest.json"
    with zipfile.ZipFile(bundle) as archive:
        if archive.namelist().count(manifest_name) != 1:
            raise ValueError("Approved whole manifest missing or duplicated")
        original_raw = archive.read(manifest_name)
    original = json.loads(original_raw)
    old_identity = bundle_identity(bundle, original["release_sha"], not_after)
    if old_identity["seed_date"] != config["state_date"]:
        raise ValueError("Approved repack state date differs from config")
    for key in ("inputs", "artifacts", "streams"):
        if not isinstance(original.get(key), dict) or not original[key]:
            raise ValueError("Approved whole manifest is incomplete: " + key)
    root_manifest = root / manifest_name
    if root_manifest.read_bytes() != original_raw:
        raise ValueError("Restored whole manifest differs from the approved archive")
    def verify_declared_source():
        for path, (old_sha, new_sha) in source_changes.items():
            source = root / path
            if (original['inputs'].get(path) != old_sha or source.is_symlink()
                    or source.resolve() != root.resolve() / path
                    or hashlib.sha256(source.read_bytes().replace(b'\r\n', b'\n')).hexdigest() != new_sha):
                raise ValueError("Approved repack actual source differs from declaration")
    verify_declared_source()
    report = {"approved_original": {**old_identity, "source": config["state_url"]},
              "config_sha256": hashlib.sha256(config_raw).hexdigest(),
              "repacked": False, "freshness": "verified_archive_only_requires_current_refresh"}
    if original["release_sha"] == strategy_sha:
        if source_changes:
            raise ValueError("Same-runtime seed cannot claim a source transition")
        return {**report, **old_identity, "unchanged_exact_runtime": True}
    # A new temporary output keeps the approved ZIP intact on every failure.
    # Retain a failed attempt directory for diagnosis, not as a recovery artifact.
    attempt = Path(tempfile.mkdtemp(prefix="approved-repack-", dir=bundle.parent))
    temporary = attempt / BUNDLE_NAME
    log = attempt / "native_pack.log"
    with log.open("wb") as stream:
        subprocess.run([sys.executable, "-B", "-u", str(root / "scripts/top100_cloud_delivery.py"),
                        "pack-archive", "--root", str(root), "--v23-root", str(root), "--v25-root", str(root),
                        "--bundle", str(temporary), "--expected-date", config["state_date"]],
                       check=True, stdout=stream, stderr=subprocess.STDOUT)
    identity = bundle_identity(temporary, strategy_sha, not_after)
    with zipfile.ZipFile(temporary) as archive:
        if archive.namelist().count(manifest_name) != 1:
            raise ValueError("Repacked whole manifest missing or duplicated")
        updated = json.loads(archive.read(manifest_name))
    _verify_repack_inputs(original['inputs'], updated.get('inputs'), source_changes)
    for key in ("expected_date", "artifacts", "streams"):
        if updated.get(key) != original[key]:
            raise ValueError("Approved repack changed economic/source manifest: " + key)
    verify_declared_source()
    if (root_manifest.read_bytes() != original_raw or config_path.read_bytes() != config_raw
            or digest(bundle) != approved_sha
            or subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip() != strategy_sha):
        raise ValueError("Approved repack input identity changed during packing")
    preserved = attempt / "approved-original.zip"
    try:
        os.link(bundle, preserved)
    except OSError:
        shutil.copyfile(bundle, preserved)
    if digest(preserved) != approved_sha:
        raise ValueError("Preserved approved archive SHA-256 differs")
    os.replace(temporary, bundle)
    return {**report, **identity, "repacked": True, "native_pack_log": str(log),
            "preserved_approved_archive": str(preserved), "economic_manifest_unchanged": True,
            "verified_source_repack_changes": source_changes}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("restore", "describe", "repack-approved"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--strategy-sha", required=True)
    parser.add_argument("--not-after-date", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--require-hit", action="store_true")
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    root, bundle = args.root.resolve(), args.bundle.resolve()
    actual_sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    if actual_sha != args.strategy_sha:
        raise ValueError("Recovery strategy SHA differs from actual checkout")
    artifact_name(args.strategy_sha)
    if (args.require_hit and args.command != "restore") or (args.config is not None and args.command != "repack-approved"):
        parser.error("maintenance config and require-hit cannot be mixed with other commands")
    if args.command == "repack-approved":
        if args.config is None:
            parser.error("repack-approved requires the bound approved config")
        try:
            report = repack_approved(root, bundle, args.strategy_sha, args.not_after_date, args.config)
        except Exception as exc:
            failed = {"repacked": False, "status": "failed", "error": f"{type(exc).__name__}: {exc}",
                      "native_pack_logs": [str(p) for p in bundle.parent.glob("approved-repack-*/native_pack.log")],
                      "freshness": "no_current_session_or_delivery_authorization"}
            args.receipt.parent.mkdir(parents=True, exist_ok=True)
            args.receipt.write_text(json.dumps(failed, indent=2) + "\n", encoding="utf8")
            print(json.dumps(failed), flush=True)
            return 1
    elif args.command == "describe":
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
