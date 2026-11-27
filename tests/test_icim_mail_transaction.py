"""Exercise the failed-alert counterexample and accepted SMTP receipt recovery."""
from __future__ import annotations

import hashlib
import io
import json
import sys
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_ic_im_v1_4_delivery as delivery
import ic_im_v1_4_mail_transaction as mail
import restore_ic_im_v1_3_ledger as ledger
import send_report


DAY = date(2026, 11, 26)
MODE = "close_confirmed"
NORMAL = delivery.marker_prefix(DAY, MODE) + "a" * 12
FAILURE = mail.alert_marker(DAY, MODE)


def result(kind):
    build, revision = delivery.expected_identity_for_day(DAY)
    return {"status": "failed" if kind == "failure" else "ok",
            "market_date": DAY.isoformat(), "publication_mode": MODE, "strategy_revision": "r1",
            "build": build, "delivery_revision": revision, "digest": "a" * 64}


def artifacts(*names):
    return {"artifacts": [{"name": name, "expired": False} for name in names]}


def unused_receipt(*args):
    raise AssertionError("unexpected SMTP receipt read")


def test_failure_marker_does_not_spend_the_later_normal_transaction():
    evidence = artifacts(FAILURE, FAILURE + mail.INTENT_SUFFIX, FAILURE + mail.RECEIPT_SUFFIX)
    assert mail.check_transaction(evidence, DAY, MODE, "failure", unused_receipt) == ("delivered", None)
    assert mail.check_transaction(evidence, DAY, MODE, "normal", unused_receipt) == ("due", None)


def test_failure_intent_with_unknown_terminal_state_is_never_resent():
    with pytest.raises(RuntimeError, match="uncertain outcome"):
        mail.check_transaction(artifacts(FAILURE + mail.INTENT_SUFFIX),
                               DAY, MODE, "failure", unused_receipt)
    assert mail.check_transaction(artifacts(FAILURE + mail.INTENT_SUFFIX),
                                  DAY, MODE, "normal", unused_receipt) == ("due", None)


def test_wrong_date_or_result_class_cannot_create_failure_intent():
    with pytest.raises(ValueError, match="date"):
        mail.build_transaction(result("failure"), b"{}", date(2026, 11, 27), MODE, "failure")
    with pytest.raises(ValueError, match="failed result"):
        mail.build_transaction(result("normal"), b"{}", DAY, MODE, "failure")


@pytest.mark.parametrize(("kind", "marker"), [("normal", NORMAL), ("failure", FAILURE)])
def test_cross_midnight_receipt_recovers_only_marker(kind, marker, tmp_path, monkeypatch):
    meta = tmp_path / "metadata.json"
    tx = tmp_path / "delivery-transaction.json"
    receipt = tmp_path / "smtp-accepted.json"
    meta.write_text('{"subject":"verified test", "body":"no live SMTP"}', encoding="utf-8")
    tx.write_text(json.dumps(mail.build_transaction(result(kind), meta.read_bytes(), DAY, MODE, kind)),
                  encoding="utf-8")
    calls = []
    timestamp = datetime(2026, 11, 26, 20, 43, 51, tzinfo=timezone.utc)
    class TestClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return timestamp.astimezone(tz)
    monkeypatch.setattr(send_report, "datetime", TestClock)
    monkeypatch.setattr(mail, "now_utc", lambda: datetime(2026, 11, 27, tzinfo=timezone.utc))
    monkeypatch.setattr(send_report, "mail_addresses", lambda: ("sender@example.invalid", ["peer@example.invalid"]))
    monkeypatch.setattr(send_report, "send_mail", lambda *args, **kwargs: calls.append(args[0]) or "<id@example.invalid>")
    monkeypatch.setattr(sys, "argv", ["send_report.py", str(meta),
                                     "--icim-delivery-transaction", str(tx), "--receipt", str(receipt)])
    assert send_report.main() == 0
    assert json.loads(receipt.read_bytes())["icim_delivery_marker"] == marker
    assert calls == ["verified test"]
    assert not delivery.marker_exists(artifacts(marker + mail.RECEIPT_SUFFIX), delivery.marker_prefix(DAY, MODE))

    packed = io.BytesIO()
    with zipfile.ZipFile(packed, "w") as archive:
        for source in (meta, tx, receipt):
            archive.write(source, source.name)
    raw = packed.getvalue()
    repo = "owner/repo"
    api = "https://api.example.invalid"
    artifact = {"id": 3, "workflow_run": {"id": 8, "head_sha": "b" * 40},
                "name": marker + mail.RECEIPT_SUFFIX, "size_in_bytes": len(raw),
                "archive_download_url": f"{api}/repos/{repo}/actions/artifacts/3/zip",
                "digest": "sha256:" + hashlib.sha256(raw).hexdigest()}
    run = {"id": 8, "status": "completed", "head_branch": "main", "head_sha": "b" * 40,
           "path": mail.WORKFLOW, "event": "schedule", "repository": {"full_name": repo}}
    intent = ("Persist normal-send intent before SMTP" if kind == "normal"
              else "Persist failure-alert intent before SMTP")
    steps = [{"name": intent, "conclusion": "success"},
             {"name": "Send Gmail", "conclusion": "success", "started_at": "2026-11-26T20:43:50Z",
              "completed_at": "2026-11-26T20:43:52Z"},
             {"name": "Preserve accepted SMTP receipt", "conclusion": "success"}]
    monkeypatch.setattr(mail, "_read_github", lambda url, token:
                        {"jobs": [{"name": "send", "steps": steps}], "total_count": 1} if "/jobs?" in url else run)
    monkeypatch.setattr(ledger, "download", lambda artifact, token: raw)
    def verify(*args):
        return mail.verify_smtp_receipt(repo, "fixture", api, *args)
    evidence = {"artifacts": [artifact, {"name": marker + mail.INTENT_SUFFIX}]}
    status, recovered = mail.check_transaction(evidence, DAY, MODE, kind, verify)
    assert status == "recover_marker"
    mail.write_recovered_marker(tmp_path / "recover", recovered)
    restored = json.loads((tmp_path / "recover/delivery.json").read_bytes())
    assert (restored["marker_name"], restored["message_id"]) == (marker, "<id@example.invalid>")
    assert calls == ["verified test"]
    steps[-1]["conclusion"] = "skipped"
    with pytest.raises(RuntimeError, match="does not prove acceptance"):
        mail.check_transaction(evidence, DAY, MODE, kind, verify)
