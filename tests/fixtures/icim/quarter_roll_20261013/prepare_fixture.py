"""Install hash-verified real quarter-roll fixtures into a disposable checkout.

Run from the automation repository, before strategy regression:
    python -B -X utf8 tests/fixtures/icim/quarter_roll_20261013/prepare_fixture.py --strategy-root strategy

This script never invokes a producer or migration. Existing differing files are
rejected before any write. The original source repository is never a target.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path, PurePosixPath


BASE = Path(__file__).resolve().parent


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def under(root: Path, relative: str) -> Path:
    part = PurePosixPath(relative)
    if part.is_absolute() or ".." in part.parts or "\\" in relative:
        raise RuntimeError(f"Unsafe fixture relative path: {relative!r}")
    result = root.joinpath(*part.parts).resolve()
    if not result.is_relative_to(root.resolve()):
        raise RuntimeError(f"Fixture path escaped its root: {relative!r}")
    return result


def prepare(strategy_root: Path, *, verify_only: bool = False) -> dict:
    manifest_path = BASE / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or manifest.get("fixture_id") != "icim-quarter-roll-20261013":
        raise RuntimeError("Unsupported quarter-roll fixture manifest")
    target = strategy_root.resolve(strict=True)
    if not target.is_dir():
        raise RuntimeError("The strategy checkout must be an existing directory")
    if target == Path(manifest["source_repository"]).resolve():
        raise RuntimeError("Refusing to install into the original production/source repository")
    test = under(target, manifest["target_test"]["relative_path"])
    if not test.is_file() or sha256(test) != manifest["target_test"]["sha256"]:
        raise RuntimeError("Target strategy test differs from the reviewed unchanged test")

    plan = []
    destinations = set()
    for entry in manifest["files"]:
        source = under(BASE, entry["payload_relative_path"])
        destination = under(target, entry["target_relative_path"])
        expected = entry["payload_sha256"]
        if destination in destinations:
            raise RuntimeError("Duplicate fixture destination")
        destinations.add(destination)
        if not source.is_file() or sha256(source) != expected or source.stat().st_size != entry["payload_bytes"]:
            raise RuntimeError(f"Fixture payload integrity failure: {source}")
        if expected != entry["source_sha256"] or entry["payload_bytes"] != entry["source_bytes"]:
            raise RuntimeError("This fixture must remain an exact, complete source-file copy")
        if destination.exists() and (not destination.is_file() or sha256(destination) != expected):
            raise RuntimeError(f"Refusing to overwrite a different existing file: {destination}")
        if verify_only and not destination.is_file():
            raise RuntimeError(f"Required fixture is absent: {destination}")
        plan.append((source, destination, expected))

    if len(plan) != 7 or sum(source.stat().st_size for source, _, _ in plan) != manifest["total_payload_bytes"]:
        raise RuntimeError("Incomplete real-data fixture manifest")
    copied = 0
    for source, destination, expected in plan:
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            copied += 1
        if sha256(destination) != expected:
            raise RuntimeError(f"Installed fixture hash mismatch: {destination}")
    return {
        "status": "PASS",
        "fixture_id": manifest["fixture_id"],
        "manifest_sha256": sha256(manifest_path),
        "strategy_root": str(target),
        "files_verified": len(plan),
        "files_copied": copied,
        "total_payload_bytes": manifest["total_payload_bytes"],
        "verify_only": verify_only,
        "fixture_only": True,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy-root", required=True, type=Path)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(json.dumps(prepare(args.strategy_root, verify_only=args.verify_only), ensure_ascii=False))
