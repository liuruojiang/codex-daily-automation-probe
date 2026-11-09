"""Bootstrap transport must survive the observed 504 without weakening identity."""
import hashlib
import io
import json
from pathlib import Path
import sys
import urllib.error

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import restore_microcap_member_checkpoint as restore
from test_member_split_rebalance_audit import archive, install, SHA, REPO


def seed(tmp_path, raw=b'approved seed', bundled=True):
    config = dict(schema='member_daily_seed_v1', strategy_sha=SHA,
                  url='https://github.com/liuruojiang/microcap/releases/download/reviewed/seed.zip',
                  sha256=hashlib.sha256(raw).hexdigest())
    if bundled:
        config['bundled_path'] = 'seed.zip'
        (tmp_path / 'seed.zip').write_bytes(raw)
    path = tmp_path / 'seed.json'
    path.write_text(json.dumps(config), encoding='utf8')
    return path


def offline(*args, **kwargs):
    raise AssertionError('Approved bundled seed must not require a download')


def test_bundled_seed_restores_while_release_download_is_unavailable(tmp_path, monkeypatch):
    path = seed(tmp_path)
    monkeypatch.setattr(restore.urllib.request, 'build_opener', offline)
    assert restore.seed_download(path, SHA) == b'approved seed'


@pytest.mark.parametrize('bad_path', ['../seed.zip', '/seed.zip', 'C:/seed.zip'])
def test_bundled_seed_cannot_escape_config_directory(tmp_path, monkeypatch, bad_path):
    path = seed(tmp_path)
    config = json.loads(path.read_bytes()); config['bundled_path'] = bad_path
    path.write_text(json.dumps(config))
    monkeypatch.setattr(restore.urllib.request, 'build_opener', offline)
    with pytest.raises(ValueError, match='bundled seed path'):
        restore.seed_download(path, SHA)


def test_corrupt_bundle_cannot_be_hidden_by_network_fallback(tmp_path, monkeypatch):
    path = seed(tmp_path)
    (tmp_path / 'seed.zip').write_bytes(b'changed')
    monkeypatch.setattr(restore.urllib.request, 'build_opener', offline)
    with pytest.raises(ValueError, match='SHA mismatch'):
        restore.seed_download(path, SHA)


def test_bundle_does_not_relax_exact_runtime_binding(tmp_path, monkeypatch):
    path = seed(tmp_path)
    monkeypatch.setattr(restore.urllib.request, 'build_opener', offline)
    with pytest.raises(ValueError, match='identity'):
        restore.seed_download(path, 'b' * 40)


def test_legacy_release_download_retries_only_failed_504_request(tmp_path, monkeypatch):
    path = seed(tmp_path, bundled=False)
    calls = []
    class Opener:
        def open(self, url, **kwargs):
            calls.append(url)
            if len(calls) == 1:
                raise urllib.error.HTTPError(url, 504, 'Gateway Time-out', {}, None)
            return io.BytesIO(b'approved seed')
    monkeypatch.setattr(restore.urllib.request, 'build_opener', lambda *a: Opener())
    monkeypatch.setattr(restore, 'sleep', lambda seconds: None, raising=False)
    assert restore.seed_download(path, SHA) == b'approved seed'
    assert len(calls) == 2 and calls[0] == calls[1]


@pytest.mark.parametrize('code,attempts', [(504, 3), (403, 1), (404, 1)])
def test_retry_budget_and_permanent_rejections(tmp_path, monkeypatch, code, attempts):
    path = seed(tmp_path, bundled=False); calls = []
    class Opener:
        def open(self, url, **kwargs):
            calls.append(url)
            raise urllib.error.HTTPError(url, code, 'failure', {}, None)
    monkeypatch.setattr(restore.urllib.request, 'build_opener', lambda *a: Opener())
    monkeypatch.setattr(restore, 'sleep', lambda seconds: None, raising=False)
    with pytest.raises(urllib.error.HTTPError): restore.seed_download(path, SHA)
    assert len(calls) == attempts


def test_first_next_session_restores_seed_without_release_network(tmp_path, monkeypatch):
    raw = archive(day='2026-11-06')[1]; config = seed(tmp_path, raw)
    install(monkeypatch, [])
    monkeypatch.setattr(restore.urllib.request, 'build_opener', offline)
    monkeypatch.setenv('GITHUB_TOKEN', 'isolated')
    monkeypatch.setattr(sys, 'argv', ['restore', '--repository', REPO,
        '--strategy-sha', SHA, '--seed-config', str(config), '--not-after-date',
        '2026-11-09', '--out', str(tmp_path / 'restored')])
    restore.main()
    assert json.loads((tmp_path / 'restored/checkpoint.json').read_bytes())['as_of_date'] == '2026-11-06'


def test_next_completed_checkpoint_wins_over_bootstrap_seed(tmp_path, monkeypatch):
    config = seed(tmp_path, archive(day='2026-11-06')[1])
    install(monkeypatch, [(10, archive(day='2026-11-09'))])
    monkeypatch.setattr(restore, 'seed_download', offline)
    monkeypatch.setenv('GITHUB_TOKEN', 'isolated')
    monkeypatch.setattr(sys, 'argv', ['restore', '--repository', REPO,
        '--strategy-sha', SHA, '--seed-config', str(config), '--not-after-date',
        '2026-11-10', '--out', str(tmp_path / 'restored')])
    restore.main()
    assert json.loads((tmp_path / 'restored/checkpoint.json').read_bytes())['as_of_date'] == '2026-11-09'


def test_future_seed_does_not_create_a_restored_directory(tmp_path, monkeypatch):
    config = seed(tmp_path, archive(day='2026-11-10')[1])
    install(monkeypatch, [])
    monkeypatch.setattr(restore.urllib.request, 'build_opener', offline)
    monkeypatch.setenv('GITHUB_TOKEN', 'isolated')
    monkeypatch.setattr(sys, 'argv', ['restore', '--repository', REPO,
        '--strategy-sha', SHA, '--seed-config', str(config), '--not-after-date',
        '2026-11-09', '--out', str(tmp_path / 'restored')])
    with pytest.raises(ValueError, match='later than'): restore.main()
    assert not (tmp_path / 'restored').exists()


def test_production_bundle_is_exact_reviewed_seed_for_current_runtime(monkeypatch):
    root = Path(__file__).parents[1]
    config = root / 'config/microcap_member_checkpoint.json'
    runtime = json.loads((root / 'config/microcap_member_runtime.json').read_bytes())['strategy_sha']
    monkeypatch.setattr(restore.urllib.request, 'build_opener', offline)
    raw = restore.seed_download(config, runtime)
    assert len(raw) == 85744
    assert hashlib.sha256(raw).hexdigest() == 'bfcec2d07b45d1d161d75c2f3bb6d2ab0e83eb922b38351439baa82c2f936f18'
    assert restore.checkpoint_identity(raw)['as_of_date'] == '2026-11-06'
