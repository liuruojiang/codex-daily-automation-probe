import io
import json
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import restore_microcap_verified_state as restore


def test_restore_selects_main_success_or_post_upload_cancellation(monkeypatch):
    def get(req, **kwargs):
        url = req.full_url
        if "/artifacts?" in url:
            payload = {"artifacts": [
                {"id": 3, "name": restore.ARTIFACT_NAME, "expired": False, "created_at": "2026-09-15T03:00:00Z", "archive_download_url": "https://api.invalid/repos/o/r/actions/artifacts/3/zip", "workflow_run": {"id": 3}},
                {"id": 2, "name": restore.ARTIFACT_NAME, "expired": False, "created_at": "2026-09-15T02:00:00Z", "archive_download_url": "https://api.invalid/repos/o/r/actions/artifacts/2/zip", "workflow_run": {"id": 2}},
                {"id": 1, "name": restore.ARTIFACT_NAME, "expired": False, "created_at": "2026-09-15T01:00:00Z", "archive_download_url": "https://api.invalid/repos/o/r/actions/artifacts/1/zip", "workflow_run": {"id": 1}},
            ]}
        else:
            run = int(url.rsplit("/", 1)[1])
            payload = {"id": run, "status": "completed", "conclusion": "failure" if run == 3 else ("cancelled" if run == 2 else "success"), "head_branch": "main", "path": restore.WORKFLOW_PATH}
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        return response
    monkeypatch.setattr(restore.urllib.request, "urlopen", get)
    assert restore.fetch_latest("o/r", "fake", "https://api.invalid")["id"] == 2


def test_restore_rejects_unexpected_artifact_member(tmp_path):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("../escape.zip", b"no")
    with pytest.raises(RuntimeError, match="unsafe or unexpected"):
        restore.extract(data.getvalue(), tmp_path / restore.STATE_FILE)
    assert not (tmp_path / restore.STATE_FILE).exists()


def test_restore_extracts_only_state_bundle(tmp_path):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr(restore.STATE_FILE, b"validated-state")
    destination = tmp_path / restore.STATE_FILE
    restore.extract(data.getvalue(), destination)
    assert destination.read_bytes() == b"validated-state"
