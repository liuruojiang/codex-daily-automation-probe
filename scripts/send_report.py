from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from email.utils import make_msgid
from pathlib import Path

from mail_utils import mail_addresses, send_mail
from etf_delivery_state import GitHubLedger, make_intent


def write_receipt(path: Path, receipt: dict[str, object]) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=path.name + ".", suffix=".tmp", delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(receipt, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def claim_local(path: Path, intent: dict) -> None:
    """A pre-send exclusive intent survives receipt failures and process death."""
    with path.open("x", encoding="utf-8") as handle:
        json.dump(intent, handle, ensure_ascii=False, indent=2)
        handle.write("\n");handle.flush();os.fsync(handle.fileno())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("metadata", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--durable-github-etf", action="store_true",
                        help="Commit a logical-day claim before SMTP; never automatically resend an existing claim")
    args = parser.parse_args()
    if args.durable_github_etf and args.receipt is None:
        parser.error("durable ETF delivery requires --receipt")
    metadata_bytes = args.metadata.read_bytes()
    meta = json.loads(metadata_bytes.decode("utf-8"))
    if not isinstance(meta, dict) or not isinstance(meta.get("subject"), str) or not isinstance(meta.get("body"), str):
        raise ValueError("Mail metadata requires subject and body strings")
    if "delivery_test_id" in meta:
        test_id = meta["delivery_test_id"]
        if (not isinstance(test_id, str) or re.fullmatch(r"[a-z0-9][a-z0-9-]{7,63}", test_id, flags=re.ASCII) is None
                or meta.get("status") != "OK" or meta.get("publication_mode") != "close_confirmed"
                or not meta["subject"].startswith("[发送验收测试][收盘确认]")
                or "不作为今日交易指令" not in meta["body"]
                or f"验收编号：{test_id}" not in meta["body"]):
            raise ValueError("Refusing unverified or mislabeled delivery test email")
    ledger, intent_path, intent, message_id = None, None, None, None
    if args.receipt:
        if args.receipt.exists():
            raise FileExistsError("Refusing to overwrite an existing SMTP receipt")
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        sender, recipients = mail_addresses()
        message_id = make_msgid(domain=sender.rsplit("@", 1)[1])
        intent_path = args.receipt.with_suffix(".intent.json")
        intent = {"status": "in_flight", "message_id": message_id,
                  "metadata_sha256": hashlib.sha256(metadata_bytes).hexdigest(),
                  "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        if args.durable_github_etf:
            intent = make_intent(metadata_bytes, meta, sender, recipients, message_id)
            ledger = GitHubLedger(intent, os.environ.get("GH_TOKEN", ""))
        # Local reservation first; a remote-claim failure also remains blocked
        # until reconciliation. Do not clear the intent to make a retry easier.
        claim_local(intent_path, intent)
        if ledger is not None:
            ledger.claim()
    try:
        sent_id = send_mail(
            meta["subject"], meta["body"], meta.get("attachment"),
            html_body=meta.get("html_body"), **({"message_id": message_id} if args.receipt else {}),
        )
        if args.receipt and (not isinstance(sent_id, str) or re.fullmatch(r"<[^<>\s]+@[^<>\s]+>", sent_id) is None):
            raise RuntimeError("SMTP sender returned no valid Message-ID; acceptance receipt cannot be written")
        if args.receipt and sent_id != message_id:
            raise RuntimeError("SMTP sender returned a different Message-ID; reconcile before retry")
    except BaseException:
        if intent_path is not None:
            try:write_receipt(intent_path, {**intent, "status": "delivery_unknown", "state": "delivery_unknown"})
            except Exception:pass
        if ledger is not None:
            try:ledger.finish("delivery_unknown")
            except Exception:pass
        raise
    if args.receipt:
        receipt = {
            "schema_version": 1,
            "status": "smtp_accepted",
            "message_id": message_id,
            "accepted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "metadata_sha256": hashlib.sha256(metadata_bytes).hexdigest(),
            "subject_sha256": hashlib.sha256(meta["subject"].encode("utf-8")).hexdigest(),
            "body_sha256": hashlib.sha256(meta["body"].encode("utf-8")).hexdigest(),
            "sender": sender,
            "recipients": recipients,
        }
        if ledger is not None:
            receipt.update({key: intent[key] for key in ("source_run_id", "source_run_attempt", "source_head_sha", "workflow_path", "logical_key")})
        write_receipt(args.receipt, receipt)
        # Once the local acceptance receipt exists, a remote update failure is
        # a post-send persistence fault. The original remote claim still blocks
        # automatic re-dispatch; the receipt must still be archived by CI.
        if ledger is not None:ledger.finish("smtp_accepted")
        write_receipt(intent_path, {**intent, "status": "smtp_accepted", "state": "smtp_accepted"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
