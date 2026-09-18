"""Microcap delivery guards that must not depend on IC/IM release identities."""
import io
import json
import sys
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_microcap_delivery as gate
import mail_utils
import restore_ic_im_v1_3_ledger as transport


@pytest.mark.parametrize("ssl", ["true", "false"])
def test_partial_smtp_rejection_must_fail_delivery(monkeypatch, ssl):
    for key, value in {
        "MAIL_SERVER": "smtp.invalid",
        "MAIL_PORT": "465" if ssl == "true" else "587",
        "MAIL_USERNAME": "test",
        "MAIL_PASSWORD": "not-a-secret",
        "MAIL_FROM": "a@example.invalid",
        "MAIL_TO": "b@example.invalid,c@example.invalid",
        "MAIL_USE_SSL": ssl,
    }.items():
        monkeypatch.setenv(key, value)
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    smtp.send_message.return_value = {"c@example.invalid": (550, b"refused")}
    monkeypatch.setattr(mail_utils.smtplib, "SMTP_SSL", lambda *a, **k: smtp)
    monkeypatch.setattr(mail_utils.smtplib, "SMTP", lambda *a, **k: smtp)
    with pytest.raises(mail_utils.smtplib.SMTPRecipientsRefused):
        mail_utils.send_mail("test only", "simulated body")
    assert smtp.send_message.call_count == 1


def test_realtime_and_close_have_distinct_delivery_keys():
    day = date(2026, 9, 4)
    assert gate.delivery_marker_name(day, "realtime") != gate.delivery_marker_name(day, "close_confirmed")


@pytest.mark.parametrize("complete", [False, True])
def test_pending_intent_blocks_ambiguous_smtp_but_completed_marker_wins(monkeypatch, complete):
    day = date(2026, 9, 4)
    name = gate.delivery_marker_name(day, "close_confirmed")
    artifacts = [{"name": name + "-send-intent", "expired": False}]
    if complete:
        artifacts.append({"name": name, "expired": False})
    monkeypatch.setattr(gate, "fetch_artifacts", lambda *a: {"artifacts": artifacts})
    monkeypatch.setattr(gate, "now_utc", lambda: datetime(2026, 9, 4, 9, tzinfo=timezone.utc))
    monkeypatch.setattr(sys, "argv", ["gate", "--publication-mode", "close_confirmed", "--repository", "o/r", "--token", "fake"])
    outputs = []
    monkeypatch.setattr(gate, "write_outputs", outputs.append)
    if complete:
        assert gate.main() == 0
        assert outputs[0]["should_send"] == "false"
    else:
        with pytest.raises(RuntimeError, match="BLOCKED.*uncertain"):
            gate.main()
        assert not outputs


@pytest.mark.parametrize("mode", ["realtime", "close_confirmed", "unknown"])
def test_legacy_marker_requires_exact_metadata_mode_and_date(monkeypatch, mode):
    report = {
        "name": "microcap-realtime-digest",
        "expired": False,
        "id": 42,
        "archive_download_url": "https://api.invalid/repos/o/r/actions/artifacts/42/zip",
    }
    response = MagicMock()
    response.__enter__.return_value.read.return_value = json.dumps({"artifacts": [report]}).encode()
    monkeypatch.setattr(gate.urllib.request, "urlopen", lambda *a, **k: response)
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr(
            "artifacts/metadata.json",
            json.dumps({"status": "OK", "signal_date": "2026-09-04", "publication_mode": mode}),
        )
    monkeypatch.setattr(transport, "download", lambda *a: data.getvalue())
    args = ("o/r", "fake", "https://api.invalid", {"workflow_run": {"id": 1}}, date(2026, 9, 4))
    if mode == "unknown":
        with pytest.raises(RuntimeError, match="BLOCKED"):
            gate.legacy_marker_mode(*args)
    else:
        assert gate.legacy_marker_mode(*args) == mode
    with pytest.raises(RuntimeError, match="BLOCKED"):
        gate.legacy_marker_mode(*args[:-1], date(2026, 9, 5))


def test_smtp_timeout_is_not_automatically_retried(monkeypatch):
    for key, value in {
        "MAIL_SERVER": "smtp.invalid",
        "MAIL_PORT": "465",
        "MAIL_USERNAME": "test",
        "MAIL_PASSWORD": "fake",
        "MAIL_FROM": "a@example.invalid",
        "MAIL_TO": "b@example.invalid",
    }.items():
        monkeypatch.setenv(key, value)
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    smtp.send_message.side_effect = TimeoutError("ambiguous DATA acknowledgement")
    monkeypatch.setattr(mail_utils.smtplib, "SMTP_SSL", lambda *a, **k: smtp)
    with pytest.raises(TimeoutError):
        mail_utils.send_mail("fixture", "fixture")
    assert smtp.send_message.call_count == 1
