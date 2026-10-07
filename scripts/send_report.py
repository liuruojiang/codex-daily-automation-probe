from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from mail_utils import mail_addresses, send_mail


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("metadata", type=Path)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
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
    if args.receipt:
        if args.receipt.exists():
            raise FileExistsError("Refusing to overwrite an existing SMTP receipt")
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        sender, recipients = mail_addresses()
    message_id = send_mail(
        meta["subject"],
        meta["body"],
        meta.get("attachment"),
        html_body=meta.get("html_body"),
    )
    if args.receipt:
        if not isinstance(message_id, str) or re.fullmatch(r"<[^<>\s]+@[^<>\s]+>", message_id) is None:
            raise RuntimeError("SMTP sender returned no valid Message-ID; acceptance receipt cannot be written")
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
        write_receipt(args.receipt, receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
