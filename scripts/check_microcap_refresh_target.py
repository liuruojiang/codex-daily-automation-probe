"""Require the refreshed Top100 base state to reach the independent close date."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path


DAILY_ANCHORS = ("proxy_index", "costed_nav", "panel_shadow")


def check_report(report: dict[str, object], expected_date: str) -> None:
    expected = date.fromisoformat(expected_date).isoformat()
    if not report.get("ok"):
        raise ValueError("refreshed base state did not pass its own validation")
    anchors = report.get("anchor_dates")
    if not isinstance(anchors, dict):
        raise ValueError("refreshed base state has no anchor dates")
    for name in DAILY_ANCHORS:
        actual = anchors.get(name)
        if actual != expected:
            raise ValueError(f"{name} anchor {actual!r} differs from completed session {expected}")
    proof = report.get("refresh_proof")
    if not isinstance(proof, dict) or proof.get("target_end_date") != expected:
        raise ValueError("independent refresh proof does not match completed session")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--expected-date", required=True)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError("state validation report must be an object")
    check_report(report, args.expected_date)
    print(f"Top100 base anchors and refresh proof match completed session {args.expected_date}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
