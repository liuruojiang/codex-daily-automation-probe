"""Exact report bytes are authenticated through the delivered metadata receipt."""

import hashlib
import re


def verify_report_binding(metadata, report_bytes):
    expected = metadata.get("report_sha256")
    if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
        raise ValueError("legacy report has no authenticated report_sha256 binding")
    if hashlib.sha256(report_bytes).hexdigest() != expected:
        raise ValueError("report_sha256 binding mismatch")
    return "sha256"
