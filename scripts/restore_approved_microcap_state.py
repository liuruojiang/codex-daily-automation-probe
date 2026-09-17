"""Restore one explicitly approved, hash-pinned whole state before normal refresh.

This bootstrap is explicit or the last fallback after ordinary recovery fails.
It never discovers a substitute; the strategy restore validates the whole-state
source and artifact manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

MAX_BUNDLE_BYTES = 150 * 1024 * 1024


def validate_seed(url: str, expected_sha256: str, seed_date: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != "github.com"
            or not parsed.path.startswith("/liuruojiang/microcap/releases/download/")
            or parsed.query or parsed.fragment or parsed.username or parsed.password
            or len(parsed.path.split("/")) != 7):
        raise ValueError("Approved state must be an exact microcap GitHub release asset URL")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
        raise ValueError("Approved state requires an exact SHA-256")
    if date.fromisoformat(seed_date).isoformat() != seed_date:
        raise ValueError("Approved state requires an ISO session date")


def download_seed(url: str, expected_sha256: str) -> bytes:
    # Public release asset download: never forward GitHub authentication.
    with urllib.request.urlopen(url, timeout=60) as response:
        content = response.read(MAX_BUNDLE_BYTES + 1)
    if not content or len(content) > MAX_BUNDLE_BYTES:
        raise ValueError("Approved state bundle is empty or exceeds size limit")
    actual = hashlib.sha256(content).hexdigest()
    if actual.lower() != expected_sha256.lower():
        raise ValueError(f"Approved state SHA-256 mismatch: actual={actual}")
    return content


def load_release_config(path: Path, actual_strategy_sha: str) -> dict[str, str]:
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("schema_version") != 1 or config.get("strategy_sha") != actual_strategy_sha:
        raise ValueError("Approved release config does not match the checked-out strategy SHA")
    values = {key: config.get(key, "") for key in ("state_url", "state_sha256", "state_date")}
    if not all(isinstance(value, str) and value for value in values.values()):
        raise ValueError("Approved release config is incomplete")
    validate_seed(values["state_url"], values["state_sha256"], values["state_date"])
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--url", default="")
    parser.add_argument("--sha256", default="")
    parser.add_argument("--seed-date", default="")
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    if args.config:
        if args.url or args.sha256 or args.seed_date:
            parser.error("--config cannot be mixed with explicit seed inputs")
        actual_sha = subprocess.check_output(["git", "-C", str(args.root), "rev-parse", "HEAD"], text=True).strip()
        config = load_release_config(args.config, actual_sha)
        args.url, args.sha256, args.seed_date = (config["state_url"], config["state_sha256"], config["state_date"])
    validate_seed(args.url, args.sha256, args.seed_date)
    content = download_seed(args.url, args.sha256)
    args.bundle.parent.mkdir(parents=True, exist_ok=True)
    args.bundle.write_bytes(content)
    subprocess.run([
        sys.executable, str(args.root / "scripts/top100_cloud_delivery.py"), "restore",
        "--root", str(args.root), "--bundle", str(args.bundle),
        "--expected-date", args.seed_date,
    ], check=True)
    report = {"source": args.url, "sha256": args.sha256.lower(),
              "seed_date": args.seed_date, "restored": True}
    print(json.dumps(report))
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.write("restored=true\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
