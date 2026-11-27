"""The verified producer ledger survives a later, independent mail failure."""
from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import restore_ic_im_v1_4_ledger as restore


REPOSITORY = "owner/repo"
API = "https://api.example.invalid"
ARCHIVE = b"exact originally uploaded ledger"


def evidence(failed_step):
    head = "a" * 40
    origin = {"full_name": REPOSITORY}
    run = dict(id=123, run_attempt=2, head_sha=head, head_branch="main", event="workflow_dispatch",
               status="completed", conclusion="failure", path=restore.WORKFLOW, repository=origin,
               head_repository=origin)
    artifact = dict(id=44, name=restore.ARTIFACT_NAME, workflow_run=dict(id=123, head_sha=head, head_branch="main"),
                    expired=False, size_in_bytes=len(ARCHIVE), digest="sha256:" + hashlib.sha256(ARCHIVE).hexdigest(),
                    archive_download_url=f"{API}/repos/{REPOSITORY}/actions/artifacts/44/zip",
                    created_at="2026-11-26T12:03:55.804211Z")
    steps = [dict(name=name, status="completed", conclusion="success", number=index + 1)
             for index, name in enumerate(restore.LEDGER_STEPS + restore.SEND_STEPS)]
    upload = steps[len(restore.LEDGER_STEPS) - 1]
    upload.update(started_at="2026-11-26T12:03:55Z", completed_at="2026-11-26T12:03:55Z")
    failure_index = next(index for index, step in enumerate(steps) if step["name"] == failed_step)
    steps[failure_index]["conclusion"] = "failure"
    for step in steps[failure_index + 1:]:
        step["conclusion"] = "skipped"
    job = dict(id=6, name="send", status="completed", conclusion="failure", run_id=123, run_attempt=2,
               head_sha=head, steps=steps)
    return run, artifact, {"jobs": [job], "total_count": 1}


def verify(run, artifact, jobs):
    return restore.verify_ledger_publication(run, artifact, jobs, repository=REPOSITORY, api_url=API)


@pytest.mark.parametrize("stage", restore.SEND_STEPS)
def test_later_delivery_failure_does_not_invalidate_a_completed_ledger(stage):
    run, artifact, jobs = evidence(stage)
    proof = verify(run, artifact, jobs)
    assert proof["schema"] == "icim_published_ledger_stage_v1"
    assert (proof["run_id"], proof["run_attempt"], proof["job_id"], proof["artifact_id"]) == (123, 2, 6, 44)
    assert proof["digest"] == artifact["digest"]
    assert proof["upload_completed_at"] == "2026-11-26T12:03:55Z"


@pytest.mark.parametrize("stage", restore.LEDGER_STEPS)
def test_failure_before_committed_ledger_is_not_a_restart_point(stage):
    assert verify(*evidence(stage)) is None


@pytest.mark.parametrize("mismatch", ["wrong-attempt", "other-head", "other-fork", "other-run",
                                      "no-digest", "outside-upload-time", "out-of-order-legacy",
                                      "duplicate-job", "incomplete-list", "not-mail-failure"])
def test_foreign_or_unproved_assets_cannot_reuse_the_ledger(mismatch):
    run, artifact, jobs = evidence("Send Gmail")
    job = jobs["jobs"][0]
    steps = {step["name"]: step for step in job["steps"]}
    if mismatch == "wrong-attempt":
        job["run_attempt"] = 1
    elif mismatch == "other-head":
        artifact["workflow_run"]["head_sha"] = "b" * 40
    elif mismatch == "other-fork":
        run["head_repository"] = {"full_name": "other/repo"}
    elif mismatch == "other-run":
        artifact["workflow_run"]["id"] += 1
    elif mismatch == "no-digest":
        artifact["digest"] = "sha256:unknown"
    elif mismatch == "outside-upload-time":
        artifact["created_at"] = "2026-11-26T12:03:56Z"
    elif mismatch == "out-of-order-legacy":
        steps["Upload successful ledger"]["number"] = steps["Send Gmail"]["number"] + 1
    elif mismatch == "duplicate-job":
        jobs["jobs"].append(dict(job, id=7))
        jobs["total_count"] = 2
    elif mismatch == "incomplete-list":
        jobs["total_count"] = 2
    elif mismatch == "not-mail-failure":
        steps["Send Gmail"]["conclusion"] = "success"
    assert verify(run, artifact, jobs) is None


def test_restore_reads_only_the_original_download_after_verifying_the_failed_attempt(monkeypatch):
    run, artifact, jobs = evidence("Mark digest delivered")
    accesses = []

    def get_json(request, timeout):
        accesses.append(request.full_url)
        if "/artifacts?" in accesses[-1]:
            value = {"artifacts": [artifact]}
        elif "/jobs?" in accesses[-1]:
            value = jobs
        else:
            value = run
        return io.BytesIO(json.dumps(value).encode())

    monkeypatch.setattr(restore.urllib.request, "urlopen", get_json)
    selected = restore.fetch_latest(REPOSITORY, "isolated", API)
    assert selected["_ledger_publication"]["artifact_id"] == artifact["id"]
    assert accesses[-1].endswith("/runs/123/jobs?filter=all&per_page=100")

    expected = artifact["archive_download_url"]
    def get_archive(request, timeout):
        assert request.full_url == expected
        return io.BytesIO(ARCHIVE)

    monkeypatch.setattr(restore.urllib.request, "build_opener", lambda _: SimpleNamespace(open=get_archive))
    assert restore.download(selected, "isolated") == ARCHIVE
    monkeypatch.setattr(
        restore.urllib.request, "build_opener", lambda _: SimpleNamespace(open=lambda *_a, **_k: io.BytesIO(ARCHIVE[::-1]))
    )
    with pytest.raises(RuntimeError, match="differs from the original artifact"):
        restore.download(selected, "isolated")
