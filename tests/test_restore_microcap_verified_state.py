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


@pytest.mark.parametrize('name', ['microcap-verified-state-validation', 'microcap-whole-delivery-validation-state', 'microcap-verified-state-recovery-v2-wrong'])
def test_new_recovery_never_selects_diagnostic_or_unpinned_artifact(name):
    with pytest.raises(ValueError, match='formal artifact'):
        restore.fetch_latest('o/r', 'fake', 'https://api.invalid', artifact_name=name, require_success=True)


def test_new_epoch_accepts_only_successful_exact_strategy_artifact(monkeypatch):
    name = 'microcap-verified-state-recovery-v2-' + 'a' * 40
    def get(req, **kwargs):
        if '/artifacts?' in req.full_url:
            payload = {'artifacts': [
                {'id': n, 'name': name, 'expired': False, 'created_at': str(n),
                 'archive_download_url': f'https://api.invalid/repos/o/r/actions/artifacts/{n}/zip',
                 'workflow_run': {'id': n}} for n in (3, 2, 1)]}
        else:
            n = int(req.full_url.rsplit('/', 1)[1])
            payload = {'id': n, 'status': 'completed', 'conclusion': {3:'failure',2:'cancelled',1:'success'}[n], 'head_branch': 'main', 'path': restore.WORKFLOW_PATH}
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        return response
    monkeypatch.setattr(restore.urllib.request, 'urlopen', get)
    assert restore.fetch_latest('o/r', 'fake', 'https://api.invalid', artifact_name=name, require_success=True)['id'] == 1


def test_formal_artifact_limit_accommodates_full_universe():
    assert restore.MAX_ARCHIVE_BYTES == 150 * 1024 * 1024
    assert 70_837_440 < restore.MAX_ARCHIVE_BYTES


@pytest.mark.parametrize('size,accepted', [(16, True), (17, False)])
def test_download_size_boundary(monkeypatch, size, accepted):
    monkeypatch.setattr(restore, 'MAX_ARCHIVE_BYTES', 16)
    opener = MagicMock()
    response = opener.open.return_value.__enter__.return_value
    response.read.return_value = b'x' * size
    monkeypatch.setattr(restore.urllib.request, 'build_opener', lambda *args: opener)
    artifact = {'archive_download_url': 'https://api.invalid/artifact'}
    if accepted:
        assert restore.download(artifact, 'fake') == b'x' * size
    else:
        with pytest.raises(RuntimeError, match='exceeds safety limit'):
            restore.download(artifact, 'fake')
    response.read.assert_called_once_with(17)


@pytest.mark.parametrize('size,accepted', [(16, True), (17, False)])
def test_inner_bundle_size_boundary(tmp_path, monkeypatch, size, accepted):
    monkeypatch.setattr(restore, 'MAX_ARCHIVE_BYTES', 16)
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(restore.STATE_FILE, b'x' * size)
    destination = tmp_path / restore.STATE_FILE
    if accepted:
        restore.extract(data.getvalue(), destination)
        assert destination.read_bytes() == b'x' * size
    else:
        with pytest.raises(RuntimeError, match='exceeds safety limit'):
            restore.extract(data.getvalue(), destination)
        assert not destination.exists()
