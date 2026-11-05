"""Bound late SMTP acceptance to its trusted original job; never resend unknown mail."""
import hashlib
import io
import json
import sys
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_microcap_delivery as gate
import restore_ic_im_v1_3_ledger as helper

DAY = date(2026, 11, 2)
NOW = datetime(2026, 11, 3, 1, tzinfo=timezone.utc)
MARKER = gate.delivery_marker_name(DAY, "close_confirmed")


def trusted_receipt(change=None):
    metadata = {"status": "OK", "publication_mode": "close_confirmed",
                "signal_date": DAY.isoformat(), "subject": "close", "body": "frozen"}
    raw = json.dumps(metadata).encode()
    receipt = {"schema_version": 1, "status": "smtp_accepted", "message_id": "<fixture@example.test>",
               "accepted_at": "2026-11-02T16:05:00+00:00",
               "metadata_sha256": hashlib.sha256(raw).hexdigest(),
               "subject_sha256": hashlib.sha256(b"close").hexdigest(),
               "body_sha256": hashlib.sha256(b"frozen").hexdigest(),
               "sender": "a@example.test", "recipients": ["b@example.test"]}
    run = {"path": ".github/workflows/microcap-realtime-digest.yml",
           "run_started_at": "2026-11-02T14:00:00Z"}
    send = {"name": "Send Gmail", "conclusion": "success", "started_at": "2026-11-02T16:04:00Z",
            "completed_at": "2026-11-02T16:06:00Z"}
    job = {"name": "send", "started_at": "2026-11-02T14:05:00Z",
           "completed_at": "2026-11-02T16:07:00Z", "steps": [send,
               {"name": "Preserve accepted SMTP receipt", "conclusion": "success"}]}
    if change:
        change(run, job, send, receipt)
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("metadata.json", raw)
        archive.writestr("smtp-accepted.json", json.dumps(receipt))
    artifact = {"id": 7, "workflow_run": {"id": 42},
                "archive_download_url": "https://api.github.com/repos/o/r/actions/artifacts/7/zip"}
    responses = [io.BytesIO(json.dumps(v).encode()) for v in (run, {"jobs": [job]})]
    return artifact, responses, payload.getvalue()


def verify(change=None):
    artifact, responses, payload = trusted_receipt(change)
    with patch.object(gate, "now_utc", return_value=NOW), \
            patch.object(gate.urllib.request, "urlopen", side_effect=responses), \
            patch.object(helper, "download", return_value=payload):
        gate.verify_smtp_receipt("o/r", "fixture", "https://api.github.com", artifact, DAY, "close_confirmed")


def test_late_receipt_accepted_only_inside_trusted_original_close_job():
    verify()


@pytest.mark.parametrize("change", [
    lambda r, j, s, p: r.pop("run_started_at"),
    lambda r, j, s, p: r.update(run_started_at="2026-11-02T16:00:00Z"),
    lambda r, j, s, p: j.update(completed_at="2026-11-02T19:00:00Z"),
    lambda r, j, s, p: j.update(completed_at="2026-11-03T02:00:00Z"),
    lambda r, j, s, p: s.update(completed_at="2026-11-02T16:04:30Z"),
    lambda r, j, s, p: s.update(started_at="2026-11-02T16:05:30Z"),
    lambda r, j, s, p: j.update(started_at="2026-11-02T14:05:00"),
    lambda r, j, s, p: p.update(accepted_at="2026-11-03T02:00:00Z"),
])
def test_unbounded_wrong_day_future_or_outside_step_receipt_remains_blocked(change):
    with pytest.raises(RuntimeError, match="BLOCKED"):
        verify(change)


def gate_outputs(now, artifacts, actual_receipt=False):
    output = []
    artifact, responses, payload = trusted_receipt()
    def fetch(repo, token, name, api):
        return {"artifacts": [dict(artifact, name=name, expired=False)] if name in artifacts else []}
    with patch.object(gate, "now_utc", return_value=now), \
            patch.object(gate, "fetch_artifacts", side_effect=fetch), \
            patch.object(gate.urllib.request, "urlopen", side_effect=responses), \
            patch.object(helper, "download", return_value=payload), \
            patch.object(gate, "write_outputs", side_effect=output.append), \
            patch.object(sys, "argv", ["gate", "--publication-mode", "close_confirmed",
                                       "--repository", "o/r", "--token", "fixture"]):
        assert gate.main() == 0
    return output[0]


def test_morning_prior_receipt_recovers_original_marker_without_smtp():
    result = gate_outputs(NOW, {MARKER + "-smtp-accepted", MARKER + "-send-intent"})
    assert result["delivery_date"] == DAY.isoformat()
    assert result["marker_name"] == MARKER
    assert result["recover_marker"] == "true" and result["should_send"] == "false"


def test_morning_without_prior_evidence_keeps_current_delivery_identity():
    result = gate_outputs(NOW, set())
    assert result["delivery_date"] == "2026-11-03" and result["recover_marker"] == "false"


def test_morning_prior_intent_without_receipt_is_unknown_and_cannot_resend():
    with pytest.raises(RuntimeError, match="previous close SMTP intent"):
        gate_outputs(NOW, {MARKER + "-send-intent"})


def test_after_close_new_day_is_not_suppressed_by_previous_receipt():
    result = gate_outputs(datetime(2026, 11, 3, 8, tzinfo=timezone.utc), {MARKER + "-smtp-accepted"})
    assert result["delivery_date"] == "2026-11-03" and result["should_send"] == "true"


def test_legacy_recovery_intent_blocks_normal_same_date_send():
    with pytest.raises(RuntimeError, match="recovery SMTP intent"):
        gate_outputs(datetime(2026, 11, 2, 10, tzinfo=timezone.utc),
                     {"microcap-close-recovery-20261102-send-intent"})


def test_completed_one_off_workflow_is_retired_and_uses_shared_concurrency():
    workflow = yaml.safe_load((ROOT / ".github/workflows/microcap-close-recovery.yml").read_text(encoding="utf-8"))
    assert workflow["jobs"]["recover"]["if"] == "${{ false }}"
    assert workflow["concurrency"]["group"] == "microcap-realtime-digest"


def test_explicit_catch_up_uses_original_normal_marker_and_preserves_intent_guard(monkeypatch):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("EXTERNAL_SCHEDULE", "false")
    output=[]
    argv=["gate","--catch-up-date","2026-11-02","--publication-mode","close_confirmed","--repository","o/r","--token","fixture"]
    with patch.object(gate,"now_utc",return_value=NOW), patch.object(sys,"argv",argv), patch.object(gate,"write_outputs",side_effect=output.append), patch.object(gate,"fetch_artifacts",return_value={"artifacts":[]}):
        assert gate.main() == 0
    assert output[0]["delivery_date"] == "2026-11-02" and output[0]["marker_name"] == MARKER
    assert output[0]["should_send"] == "true"
    def fetch(repo,token,name,api):
        return {"artifacts":[{"name":name,"expired":False}]} if name == MARKER+"-send-intent" else {"artifacts":[]}
    with patch.object(gate,"now_utc",return_value=NOW), patch.object(sys,"argv",argv), patch.object(gate,"fetch_artifacts",side_effect=fetch):
        with pytest.raises(RuntimeError,match="prior SMTP send intent"):
            gate.main()

@pytest.mark.parametrize("fault", ["today","future","format","realtime","correction","validation","test","schedule","external"])
def test_explicit_catch_up_never_waives_normal_identity(monkeypatch,fault):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("EXTERNAL_SCHEDULE", "false")
    kw=dict(publication_mode="close_confirmed",correction=False,validation_only=False,test_id="")
    day="2026-11-02"
    if fault=="today":day="2026-11-03"
    elif fault=="future":day="2026-11-04"
    elif fault=="format":day="20261102"
    elif fault=="realtime":kw['publication_mode']='realtime'
    elif fault=="correction":kw['correction']=True
    elif fault=="validation":kw['validation_only']=True
    elif fault=="test":kw['test_id']='other'
    elif fault=="schedule":monkeypatch.setenv("GITHUB_EVENT_NAME","schedule")
    else:monkeypatch.setenv("EXTERNAL_SCHEDULE","true")
    with pytest.raises(ValueError):gate.validate_catch_up_date(day,NOW,**kw)


def test_catch_up_target_must_match_real_completed_calendar_before_expensive_restore():
    from check_microcap_refresh_target import validate_catch_up_completed
    validate_catch_up_completed("2026-11-05","2026-11-05")
    for requested in ("2026-11-04","2026-11-06","20261105"):
        with pytest.raises(ValueError):validate_catch_up_completed(requested,"2026-11-05")
    workflow=yaml.safe_load((ROOT/'.github/workflows/microcap-realtime-digest.yml').read_text(encoding='utf8'))
    steps=workflow['jobs']['send']['steps']
    resolve=next(x for x in steps if x['name']=='Resolve independent completed session')
    assert 'validate_catch_up_completed' in resolve['run']
    assert 'latest_completed_session()' in resolve['run']
    assert steps.index(resolve)<next(i for i,x in enumerate(steps) if x['name']=='Restore explicitly approved whole state')


def test_explicit_manual_catch_up_receipt_recovers_without_resending(monkeypatch):
    artifact,responses,payload=trusted_receipt()
    run=json.loads(responses[0].getvalue());jobs=json.loads(responses[1].getvalue())
    run.update(event="workflow_dispatch",run_started_at="2026-11-02T16:00:00Z")
    jobs['jobs'][0]['started_at']="2026-11-02T16:01:00Z"
    jobs['jobs'][0]['steps'].append({'name':'Resolve independent completed session','conclusion':'success'})
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        meta=json.loads(z.read('metadata.json'));receipt=json.loads(z.read('smtp-accepted.json'))
    meta['catch_up_date']=DAY.isoformat();raw=json.dumps(meta).encode()
    receipt['metadata_sha256']=hashlib.sha256(raw).hexdigest()
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as z:
        z.writestr('metadata.json',raw);z.writestr('smtp-accepted.json',json.dumps(receipt))
    for event,success in [('workflow_dispatch',True),('schedule',False)]:
        run['event']=event
        streams=[io.BytesIO(json.dumps(v).encode()) for v in (run,jobs)]
        with patch.object(gate,'now_utc',return_value=NOW),patch.object(gate.urllib.request,'urlopen',side_effect=streams),patch.object(helper,'download',return_value=buffer.getvalue()):
            if success:gate.verify_smtp_receipt('o/r','fixture','https://api.github.com',artifact,DAY,'close_confirmed')
            else:
                with pytest.raises(RuntimeError,match='cross-midnight'):
                    gate.verify_smtp_receipt('o/r','fixture','https://api.github.com',artifact,DAY,'close_confirmed')
