"""Require the refreshed Top100 base state to reach the independent close date."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


DAILY_ANCHORS = ("proxy_index", "costed_nav", "panel_shadow")
REFRESH_PROOF_VERSION = 1
REFRESH_PROOF_SOURCE = "independent_close_history_refresh"


def cn_today() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=8)).date().isoformat()


def check_report(report: dict[str, object], expected_date: str, verified_on: str) -> None:
    parsed_expected = date.fromisoformat(expected_date)
    if parsed_expected.isoformat() != expected_date:
        raise ValueError("completed session must use exact YYYY-MM-DD format")
    expected = parsed_expected.isoformat()
    if report.get("ok") is not True:
        raise ValueError("refreshed base state did not pass its own validation")
    errors = report.get("errors")
    if not isinstance(errors, list) or errors:
        raise ValueError("refreshed base state contains validation errors")
    anchors = report.get("anchor_dates")
    if not isinstance(anchors, dict):
        raise ValueError("refreshed base state has no anchor dates")
    for name in DAILY_ANCHORS:
        actual = anchors.get(name)
        if actual != expected:
            raise ValueError(f"{name} anchor {actual!r} differs from completed session {expected}")
    proof = report.get("refresh_proof")
    if not isinstance(proof, dict):
        raise ValueError("independent refresh proof is missing or invalid")
    if type(proof.get("version")) is not int or proof.get("version") != REFRESH_PROOF_VERSION:
        raise ValueError("independent refresh proof has an unsupported version")
    if proof.get("source") != REFRESH_PROOF_SOURCE:
        raise ValueError("independent refresh proof has an unrecognized source")
    if proof.get("target_end_date") != expected:
        raise ValueError("independent refresh proof does not match completed session")
    if proof.get("verified_on") != verified_on:
        raise ValueError("independent refresh proof is not verified for today")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--expected-date", required=True)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError("state validation report must be an object")
    check_report(report, args.expected_date, cn_today())
    print(f"Top100 base anchors and refresh proof match completed session {args.expected_date}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
