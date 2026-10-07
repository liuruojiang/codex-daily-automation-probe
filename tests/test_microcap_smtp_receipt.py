"""SMTP acceptance is traceable, atomic, and never inferred from a local build."""
from __future__ import annotations

import hashlib
import json
import re
import smtplib
import sys
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import mail_utils
import send_report


@pytest.fixture
def smtp_fixture(monkeypatch):
    values = {
        "MAIL_SERVER": "smtp.invalid",
        "MAIL_PORT": "465",
        "MAIL_USERNAME": "fixture-user",
        "MAIL_PASSWORD": "fixture-password-DO-NOT-RECORD",
        "MAIL_FROM": "Fixture Sender <sender@example.invalid>",
        "MAIL_TO": "First <first@example.invalid>,second@example.invalid",
        "MAIL_USE_SSL": "true",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    smtp.send_message.return_value = {}
    monkeypatch.setattr(mail_utils.smtplib, "SMTP_SSL", lambda *a, **k: smtp)
    monkeypatch.setattr(mail_utils.smtplib, "SMTP", lambda *a, **k: smtp)
    return smtp, values


def configure_tls(monkeypatch, tls):
    monkeypatch.setenv("MAIL_PORT", "465" if tls == "ssl" else "587")
    monkeypatch.setenv("MAIL_USE_SSL", "true" if tls == "ssl" else "false")


def metadata_file(tmp_path, value=None):
    path = tmp_path / "metadata.json"
    payload = value if value is not None else {"status": "OK", "subject": "隔离验收测试", "body": "仅为隔离运输测试，不发送。"}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("tls", ["ssl", "starttls"])
def test_accepted_send_has_standard_headers_and_exact_atomic_receipt(tmp_path, monkeypatch, smtp_fixture, tls):
    smtp, credentials = smtp_fixture
    configure_tls(monkeypatch, tls)
    path = metadata_file(tmp_path)
    receipt_path = tmp_path / "new" / "accepted.json"
    monkeypatch.setattr(sys, "argv", ["send_report.py", str(path), "--receipt", str(receipt_path)])
    assert send_report.main() == 0
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    message = smtp.send_message.call_args.args[0]
    metadata = json.loads(path.read_text(encoding="utf-8"))
    assert receipt["schema_version"] == 1
    assert receipt["status"] == "smtp_accepted"
    assert receipt["message_id"] == message["Message-ID"]
    assert re.fullmatch(r"<[^<>\s]+@example\.invalid>", receipt["message_id"])
    assert parsedate_to_datetime(message["Date"]).utcoffset() == timedelta(0)
    assert datetime.fromisoformat(receipt["accepted_at"]).utcoffset() == timedelta(0)
    assert receipt["metadata_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert receipt["subject_sha256"] == hashlib.sha256(metadata["subject"].encode("utf-8")).hexdigest()
    assert receipt["body_sha256"] == hashlib.sha256(metadata["body"].encode("utf-8")).hexdigest()
    assert receipt["sender"] == "sender@example.invalid"
    assert receipt["recipients"] == ["first@example.invalid", "second@example.invalid"]
    rendered = receipt_path.read_text(encoding="utf-8")
    for key in ("MAIL_USERNAME", "MAIL_PASSWORD", "MAIL_SERVER"):
        assert key not in rendered
        assert credentials[key] not in rendered
    assert not list(receipt_path.parent.glob("*.tmp"))
    assert smtp.send_message.call_count == 1
    assert smtp.starttls.call_count == (tls == "starttls")


def test_different_accepted_messages_have_different_message_ids(smtp_fixture):
    first = mail_utils.send_mail("fixture", "fixture")
    second = mail_utils.send_mail("fixture", "fixture")
    assert first != second


@pytest.mark.parametrize("tls", ["ssl", "starttls"])
@pytest.mark.parametrize("fault", ["authentication", "partial_refusal", "all_refused", "data_timeout", "disconnect", "quit_timeout"])
def test_smtp_failure_writes_no_receipt_and_never_retries(tmp_path, monkeypatch, smtp_fixture, tls, fault):
    smtp, _ = smtp_fixture
    configure_tls(monkeypatch, tls)
    if fault == "authentication":
        smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b"fixture rejected")
    elif fault == "partial_refusal":
        smtp.send_message.return_value = {"second@example.invalid": (550, b"fixture rejected")}
    elif fault == "all_refused":
        smtp.send_message.side_effect = smtplib.SMTPRecipientsRefused({"first@example.invalid": (550, b"fixture rejected")})
    elif fault == "data_timeout":
        smtp.send_message.side_effect = TimeoutError("fixture uncertain DATA acknowledgement")
    elif fault == "disconnect":
        smtp.send_message.side_effect = smtplib.SMTPServerDisconnected("fixture disconnected")
    elif fault == "quit_timeout":
        smtp.__exit__.side_effect = TimeoutError("fixture QUIT timeout after accepted DATA")
    path = metadata_file(tmp_path)
    receipt_path = tmp_path / "accepted.json"
    monkeypatch.setattr(sys, "argv", ["send_report.py", str(path), "--receipt", str(receipt_path)])
    with pytest.raises((smtplib.SMTPException, TimeoutError)):
        send_report.main()
    assert not receipt_path.exists()
    assert not list(tmp_path.glob("*.tmp"))
    assert smtp.send_message.call_count == (0 if fault == "authentication" else 1)


def test_receipt_publish_failure_is_not_success_and_does_not_resend(tmp_path, monkeypatch, smtp_fixture):
    smtp, _ = smtp_fixture
    path = metadata_file(tmp_path)
    receipt_path = tmp_path / "accepted.json"
    monkeypatch.setattr(sys, "argv", ["send_report.py", str(path), "--receipt", str(receipt_path)])
    def refuse_replace(*args):
        raise OSError("fixture atomic publish failed")
    monkeypatch.setattr(send_report.os, "replace", refuse_replace)
    with pytest.raises(OSError, match="publish failed"):
        send_report.main()
    assert smtp.send_message.call_count == 1
    assert not receipt_path.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_existing_receipt_is_not_overwritten_and_does_not_send(tmp_path, monkeypatch, smtp_fixture):
    smtp, _ = smtp_fixture
    path = metadata_file(tmp_path)
    receipt_path = tmp_path / "accepted.json"
    receipt_path.write_text("previous acceptance proof\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["send_report.py", str(path), "--receipt", str(receipt_path)])
    with pytest.raises(FileExistsError):
        send_report.main()
    assert receipt_path.read_text(encoding="utf-8") == "previous acceptance proof\n"
    smtp.send_message.assert_not_called()


@pytest.mark.parametrize("tls", ["ssl", "starttls"])
def test_legacy_single_metadata_usage_still_sends_without_receipt(tmp_path, monkeypatch, smtp_fixture, tls):
    smtp, _ = smtp_fixture
    configure_tls(monkeypatch, tls)
    path = metadata_file(tmp_path)
    monkeypatch.setattr(sys, "argv", ["send_report.py", str(path)])
    assert send_report.main() == 0
    assert smtp.send_message.call_count == 1
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("invalid_id", [None, "", "unproven", "<missing-domain>"])
def test_receipt_requires_successful_sender_message_id(tmp_path, monkeypatch, smtp_fixture, invalid_id):
    path = metadata_file(tmp_path)
    receipt_path = tmp_path / "accepted.json"
    monkeypatch.setattr(sys, "argv", ["send_report.py", str(path), "--receipt", str(receipt_path)])
    monkeypatch.setattr(send_report, "send_mail", lambda *a, **k: invalid_id)
    with pytest.raises(RuntimeError, match="no valid Message-ID"):
        send_report.main()
    assert not receipt_path.exists()


@pytest.mark.parametrize("metadata", [{"subject": "fixture"}, {"subject": None, "body": "fixture"}, []])
def test_invalid_metadata_is_rejected_before_smtp(tmp_path, monkeypatch, smtp_fixture, metadata):
    smtp, _ = smtp_fixture
    path = metadata_file(tmp_path, metadata)
    monkeypatch.setattr(sys, "argv", ["send_report.py", str(path), "--receipt", str(tmp_path / "accepted.json")])
    with pytest.raises(ValueError, match="requires subject and body"):
        send_report.main()
    smtp.send_message.assert_not_called()


@pytest.mark.parametrize("key,value", [("MAIL_TO", "first@example.invalid\r\nBcc: second@example.invalid"), ("MAIL_FROM", "sender@example.invalid,other@example.invalid"), ("MAIL_TO", "@example.invalid"), ("MAIL_TO", "invalid-address")])
def test_invalid_recipient_or_sender_configuration_does_not_open_smtp(monkeypatch, smtp_fixture, key, value):
    smtp, _ = smtp_fixture
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        mail_utils.send_mail("fixture", "fixture")
    smtp.login.assert_not_called()
    smtp.send_message.assert_not_called()
