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
import tempfile
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

MAX_BUNDLE_BYTES = 2 * 1024 * 1024 * 1024
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
EXACT_301288_PACKAGE = '3af53c7fea86b3334321112981747560e9fcea9dba5ee7667c9f011d8a70f4ff'
EXACT_603028_PACKAGE = '7fe387b7a1fbcd8548fff0533852c6421d7b3c59502cee361d0da86734b18dad'
EXACT_301288_PATHS = {'scripts/share_unchanged_tail.py', 'data/share_source_future_plan_reviews.json',
                       'scripts/realtime_state_bundle.py', 'scripts/sse_share_daily_unchanged_20261104.py'}


def source_successor(config: dict) -> dict | None:
    """Retain the accepted original restore identity for this exact source repair."""
    transition = config.get('approved_source_successor')
    if transition is None:
        return None
    if (set(transition) != {'bootstrap_sha', 'approval_package_sha256', 'changes'}
            or transition['bootstrap_sha'] != '6fa0b9d73b12fff548200d987c953d1b1a7d74d7'
            or not ((transition['approval_package_sha256'] == EXACT_301288_PACKAGE
                     and set(transition['changes']) in (EXACT_301288_PATHS, EXACT_301288_PATHS | {'scripts/st_notice_boundary_review.py'}))
                    or (transition['approval_package_sha256'] == EXACT_603028_PACKAGE
                        and set(transition['changes']) in (
                            EXACT_301288_PATHS | {'scripts/st_notice_boundary_review.py', 'scripts/share_change_source.py'},
                            EXACT_301288_PATHS | {'scripts/st_notice_boundary_review.py', 'scripts/share_change_source.py', 'microcap_top100_mom16_biweekly_live_v2_0.py'})))):
        raise ValueError('Unreviewed approved-source successor')
    for pair in transition['changes'].values():
        if (not isinstance(pair, list) or len(pair) != 2 or pair[0] == pair[1]
                or any(not isinstance(x, str) or re.fullmatch('[0-9a-f]{64}', x) is None for x in pair)):
            raise ValueError('Approved-source successor requires exact distinct old/new hashes')
    return transition


def restore_with_source_successor(root: Path, bundle: Path, seed_date: str, config: dict) -> None:
    """Native restore under original code, then exact reviewed source switch.

    No manifest, market input, historical result or recovery receipt is relabelled.
    This is preparation only: the existing normal refresh/whole/send gates follow.
    """
    transition = source_successor(config)
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args])
    runtime = config['strategy_sha']
    if git('rev-parse', 'HEAD').decode().strip() != runtime:
        raise ValueError('Successor checkout is not the configured runtime')
    dirty = set(git('diff', '--name-only').decode().splitlines()) | set(git('diff', '--cached', '--name-only').decode().splitlines())
    if any(not p.startswith(('outputs/', '.microcap_index_cache/')) for p in dirty):
        raise ValueError('Successor restore requires an unchanged source checkout')
    previous = transition['bootstrap_sha']
    subprocess.run(['git', '-C', str(root), 'fetch', '--no-tags', 'origin', previous], check=True)
    # Compare the complete trees, including a shallow Actions checkout. A
    # history traversal would require an unrelated full-history download.
    changed = set(git('diff', '--name-only', previous, runtime).decode().splitlines())
    execution = {p for p in changed if not p.startswith(('tests/', 'docs/'))}
    if execution != set(transition['changes']):
        raise ValueError('Successor contains an unrelated execution change')
    for name, pair in transition['changes'].items():
        for revision, expected in zip((previous, runtime), pair):
            if hashlib.sha256(git('show', revision + ':' + name).replace(b'\r\n', b'\n')).hexdigest() != expected:
                raise ValueError('Successor Git source hash differs: ' + name)
    subprocess.run(['git', '-C', str(root), 'checkout', '--detach', previous], check=True)
    try:
        subprocess.run([sys.executable, str(root/'scripts/top100_cloud_delivery.py'), 'restore',
                        '--root', str(root), '--bundle', str(bundle), '--expected-date', seed_date], check=True)
    finally:
        subprocess.run(['git', '-C', str(root), 'checkout', '--detach', runtime], check=True)
    for name, pair in transition['changes'].items():
        if hashlib.sha256((root/name).read_bytes().replace(b'\r\n', b'\n')).hexdigest() != pair[1]:
            raise ValueError('Restored successor source readback differs: ' + name)


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


def download_seed(url: str, expected_sha256: str, destination: Path) -> Path:
    # Public release asset download: never forward GitHub authentication.
    # Verify the actual body before replacing any existing approved bundle.
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", prefix=destination.name + ".",
                                         suffix=".partial", dir=destination.parent,
                                         delete=False) as target:
            partial = Path(target.name)
            digest = hashlib.sha256()
            count = 0
            with urllib.request.urlopen(url, timeout=60) as response:
                while chunk := response.read(DOWNLOAD_CHUNK_BYTES):
                    count += len(chunk)
                    if count > MAX_BUNDLE_BYTES:
                        raise ValueError("Approved state bundle exceeds size limit")
                    digest.update(chunk)
                    target.write(chunk)
            if count == 0:
                raise ValueError("Approved state bundle is empty")
            actual = digest.hexdigest()
            if actual.lower() != expected_sha256.lower():
                raise ValueError(f"Approved state SHA-256 mismatch: actual={actual}")
        os.replace(partial, destination)
        partial = None
        return destination
    finally:
        if partial is not None:
            partial.unlink(missing_ok=True)


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
    full_config = None
    if args.config:
        if args.url or args.sha256 or args.seed_date:
            parser.error("--config cannot be mixed with explicit seed inputs")
        actual_sha = subprocess.check_output(["git", "-C", str(args.root), "rev-parse", "HEAD"], text=True).strip()
        config = load_release_config(args.config, actual_sha)
        full_config = json.loads(args.config.read_bytes())
        args.url, args.sha256, args.seed_date = (config["state_url"], config["state_sha256"], config["state_date"])
    validate_seed(args.url, args.sha256, args.seed_date)
    download_seed(args.url, args.sha256, args.bundle)
    if full_config is not None and source_successor(full_config) is not None:
        restore_with_source_successor(args.root, args.bundle, args.seed_date, full_config)
    else:
        subprocess.run([
            sys.executable, str(args.root / "scripts/top100_cloud_delivery.py"), "restore",
            "--root", str(args.root), "--bundle", str(args.bundle),
            "--expected-date", args.seed_date,
        ], check=True)
    report = {"source": args.url, "sha256": args.sha256.lower(),
              "seed_date": args.seed_date, "restored": True}
    if full_config is not None and source_successor(full_config) is not None:
        report['approved_source_successor'] = source_successor(full_config)
        report['current_state_verified'] = False
    print(json.dumps(report))
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.write("restored=true\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
