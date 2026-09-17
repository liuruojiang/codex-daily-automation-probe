import hashlib
import io
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import restore_approved_microcap_state as seed

URL = 'https://github.com/liuruojiang/microcap/releases/download/approved-test/state.zip'


@pytest.mark.parametrize('url', ['http://github.com/liuruojiang/microcap/releases/download/tag/state.zip', 'https://example.com/state.zip', URL + '?x=1', URL.replace('/microcap/', '/another/')])
def test_untrusted_asset_rejected(url):
    with pytest.raises(ValueError):
        seed.validate_seed(url, 'a' * 64, '2026-09-17')


def test_exact_release_identity_required():
    seed.validate_seed(URL, 'a' * 64, '2026-09-17')
    with pytest.raises(ValueError):
        seed.validate_seed(URL, 'not-a-hash', '2026-09-17')
    with pytest.raises(ValueError):
        seed.validate_seed(URL, 'a' * 64, '2026-09-99')


def test_download_checks_hash_before_returning_bytes():
    data = b'exact approved test bytes'
    with patch.object(seed.urllib.request, 'urlopen', return_value=io.BytesIO(data)):
        assert seed.download_seed(URL, hashlib.sha256(data).hexdigest()) == data
    with patch.object(seed.urllib.request, 'urlopen', return_value=io.BytesIO(data)):
        with pytest.raises(ValueError, match='SHA-256 mismatch'):
            seed.download_seed(URL, 'a' * 64)


def test_failed_whole_manifest_restore_never_reports_success(tmp_path, monkeypatch):
    data = b'approved bytes'
    output = tmp_path / 'output'
    monkeypatch.setenv('GITHUB_OUTPUT', str(output))
    monkeypatch.setattr(sys, 'argv', ['restore', '--root', str(tmp_path / 'strategy'), '--bundle', str(tmp_path / 'bundle.zip'), '--url', URL, '--sha256', hashlib.sha256(data).hexdigest(), '--seed-date', '2026-09-17'])
    monkeypatch.setattr(seed, 'download_seed', lambda *args: data)
    def reject(command, **kwargs):
        assert 'scripts' in command[1] and 'top100_cloud_delivery.py' in command[1]
        assert command[2] == 'restore'
        assert kwargs == {'check': True}
        raise seed.subprocess.CalledProcessError(1, command)
    monkeypatch.setattr(seed.subprocess, 'run', reject)
    with pytest.raises(seed.subprocess.CalledProcessError):
        seed.main()
    assert not output.exists()


def test_explicit_seed_precedes_and_excludes_old_state_restore():
    workflow = yaml.safe_load((ROOT / '.github/workflows/microcap-realtime-digest.yml').read_text(encoding='utf-8'))
    steps = workflow['jobs']['send']['steps']
    lookup = {step.get('name'): step for step in steps}
    approved = lookup['Restore explicitly approved whole state']
    assert "inputs.approved_state_url != ''" in approved['if']
    for name in ['Restore verified production state bundle', 'Restore durable verified production state bundle', 'Restore verified production state']:
        assert "inputs.approved_state_url == ''" in lookup[name]['if']
        assert steps.index(approved) < steps.index(lookup[name])
    assert "steps.approved_state.outputs.restored == 'true'" in lookup['Refresh Top100 realtime state']['if']
    on = workflow.get('on', workflow.get(True))
    assert on['workflow_dispatch']['inputs']['approved_state_url']['default'] == ''

