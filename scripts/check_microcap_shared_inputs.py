"""Bind sequential sibling generation to the strategy's existing input hashes."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import re
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("capture", "check"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--expected-date", required=True)
    args = parser.parse_args()
    if date.fromisoformat(args.expected_date).isoformat() != args.expected_date:
        raise ValueError("Expected an ISO exchange session")
    root = args.root.resolve()
    sys.path.insert(0, str(root))
    from scripts.top100_delivery import input_hashes

    current = input_hashes(root)
    if not current or any(not isinstance(k, str) or not isinstance(v, str)
                          or not re.fullmatch(r"[0-9a-f]{64}", v) for k, v in current.items()):
        raise ValueError("Invalid strategy input fingerprint")
    identity = {"schema_version": 1, "root": str(root),
                "expected_date": args.expected_date, "inputs": current}
    if args.command == "capture":
        args.snapshot.parent.mkdir(parents=True, exist_ok=True)
        with args.snapshot.open("x", encoding="utf-8") as handle:
            json.dump(identity, handle, indent=2)
        print("Captured shared inputs before three-version generation")
    else:
        previous = json.loads(args.snapshot.read_text(encoding="utf-8"))
        if previous != identity:
            raise ValueError("Shared inputs changed during three-version generation; publication blocked")
        print("Shared inputs unchanged across three-version generation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
