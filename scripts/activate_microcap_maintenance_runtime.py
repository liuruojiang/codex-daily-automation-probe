"""Switch a natively restored seed to one exact reviewed maintenance-only fix.

The original restore receipt and all data remain unchanged. This grants no
historical migration, current-data freshness or daily-mail authorization.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

SOURCE = "scripts/st_notice_boundary_review.py"
DATED_SHARE_SOURCES = {SOURCE, "scripts/share_unchanged_tail.py", "scripts/share_change_source.py", "scripts/realtime_state_bundle.py"}
LOCAL_WINDOW_SOURCES = DATED_SHARE_SOURCES | {"scripts/exchange_calendar.py"}


def activate(root: Path, config_path: Path, restore_receipt: Path, receipt: Path) -> dict:
    root = root.resolve()
    config_raw, restored_raw = config_path.read_bytes(), restore_receipt.read_bytes()
    config, restored = json.loads(config_raw), json.loads(restored_raw)
    if set(config) != {"schema_version", "baseline_sha", "runtime_sha", "changes"} or config["schema_version"] != 1:
        raise ValueError("Invalid maintenance source review")
    old, new = config["baseline_sha"], config["runtime_sha"]
    if old == new or any(not isinstance(v, str) or re.fullmatch("[0-9a-f]{40}", v) is None for v in (old, new)):
        raise ValueError("Maintenance review requires distinct exact commits")
    if set(config["changes"]) not in ({SOURCE}, DATED_SHARE_SOURCES, LOCAL_WINDOW_SOURCES):
        raise ValueError("Maintenance review is limited to the native boundary and paired dated-share continuation")
    for pair in config["changes"].values():
        if (not isinstance(pair, list) or len(pair) != 2 or pair[0] == pair[1]
                or any(not isinstance(v, str) or re.fullmatch("[0-9a-f]{64}", v) is None for v in pair)):
            raise ValueError("Maintenance review requires exact source hashes")
    if restored.get("restored") is not True or restored.get("strategy_sha") != old:
        raise ValueError("Maintenance requires the actual baseline restore receipt")
    if receipt.resolve() == restore_receipt.resolve():
        raise ValueError("Maintenance cannot overwrite the original restore receipt")

    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args])

    def digest(raw):
        return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()

    if git("rev-parse", "HEAD").decode().strip() != old:
        raise ValueError("Maintenance baseline checkout differs")
    dirty = set(git("diff", "--name-only").decode().splitlines()) | set(git("diff", "--cached", "--name-only").decode().splitlines())
    if any(not name.startswith(("outputs/", ".microcap_index_cache/")) for name in dirty):
        raise ValueError("Maintenance source checkout has local edits")
    subprocess.run(["git", "-C", str(root), "fetch", "--no-tags", "origin", new], check=True)
    changed = set(git("diff", "--name-only", old, new).decode().splitlines())
    execution = {name for name in changed if not name.startswith(("tests/", "docs/"))}
    if execution != set(config["changes"]):
        raise ValueError("Maintenance runtime contains unrelated execution changes")
    for source, pair in config["changes"].items():
        for revision, expected in zip((old, new), pair):
            if digest(git("show", revision + ":" + source)) != expected:
                raise ValueError("Maintenance Git source hash differs")
        if digest((root / source).read_bytes()) != pair[0]:
            raise ValueError("Maintenance baseline source readback differs")
    if config_path.read_bytes() != config_raw or restore_receipt.read_bytes() != restored_raw:
        raise ValueError("Maintenance review or restore receipt changed concurrently")
    subprocess.run(["git", "-C", str(root), "checkout", "--detach", new], check=True)
    for source, pair in config["changes"].items():
        if digest((root / source).read_bytes()) != pair[1]:
            raise ValueError("Maintenance runtime source readback differs")
    result = dict(schema="reviewed_maintenance_runtime_v1", baseline_sha=old, runtime_sha=new,
                  changes=config["changes"], review_sha256=hashlib.sha256(config_raw).hexdigest(),
                  restore_receipt_sha256=hashlib.sha256(restored_raw).hexdigest(),
                  activated=True, current_data_verified=False)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(result, indent=2) + "\n", encoding="utf8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--restore-receipt", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(activate(args.root, args.config, args.restore_receipt, args.receipt)))


if __name__ == "__main__":
    main()
