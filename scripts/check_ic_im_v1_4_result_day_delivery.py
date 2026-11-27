"""Second dedupe gate using the signal's actual market date, before SMTP intent."""
from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path

from check_ic_im_v1_4_delivery import (
    fetch_artifacts,
    marker_exists,
    marker_prefix,
    pending_send_exists,
    write_outputs,
)
from prepare_ic_im_v1_4_marker import marker_name
from ic_im_v1_4_mail_transaction import (
    build_transaction,
    check_transaction,
    verify_smtp_receipt,
    write_recovered_marker,
)


def should_send_for_result(result: dict[str, object], artifacts: dict[str, object], *, correction: bool) -> bool:
    marker_name(result)  # Validate version, mode, date and digest before matching artifacts.
    if correction:
        return True
    prefix = marker_prefix(date.fromisoformat(str(result["market_date"])), str(result["publication_mode"]))
    if marker_exists(artifacts, prefix):
        return False
    if pending_send_exists(artifacts, prefix):
        raise RuntimeError(
            "BLOCKED: prior signal-day SMTP send intent has no completion marker; delivery is uncertain"
        )
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", required=True)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    parser.add_argument("--api-url", default=os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    parser.add_argument("--correction", action="store_true")
    parser.add_argument("--delivery-date", default="")
    parser.add_argument("--publication-mode", choices=("realtime", "close_confirmed"))
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--transaction", type=Path)
    parser.add_argument("--kind", choices=("normal", "failure"), default="normal")
    parser.add_argument("--recovery-dir", type=Path)
    args = parser.parse_args()
    result = json.loads(Path(args.result).read_text(encoding="utf-8"))
    if (args.metadata is None) != (args.transaction is None) or (args.kind == "failure" and args.metadata is None):
        parser.error("mail transactions require metadata and transaction paths")
    bypass = args.correction and args.kind == "normal"
    if not bypass and (not args.repository or not args.token):
        raise SystemExit("GITHUB_REPOSITORY and GITHUB_TOKEN are required")
    artifacts = {} if bypass else fetch_artifacts(args.repository, args.token, args.api_url)
    if args.metadata is None:
        should_send = should_send_for_result(result, artifacts, correction=bypass)
        write_outputs({"should_send_result_day": str(should_send).lower()})
        return 0
    day = date.fromisoformat(args.delivery_date or result["market_date"])
    mode = args.publication_mode or result["publication_mode"]
    transaction = build_transaction(result, args.metadata.read_bytes(), day, mode, args.kind)
    status, recovered = "correction", None
    if not bypass:
        status, recovered = check_transaction(
            artifacts, day, mode, args.kind,
            lambda *values: verify_smtp_receipt(args.repository, args.token, args.api_url, *values),
        )
    args.transaction.write_text(json.dumps(transaction, indent=2) + "\n", encoding="utf-8")
    write_recovered_marker(args.recovery_dir, recovered)
    write_outputs({
        "should_send_result_day": str(status in {"due", "correction"}).lower(),
        "recover_marker": str(recovered is not None).lower(),
        "marker_name": recovered["marker_name"] if recovered else transaction["marker_name"],
        "delivery_status": status,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
