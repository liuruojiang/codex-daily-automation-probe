"""Independent counterexamples for restoring the next day's published checkpoint.

All transport and checkpoint inputs in this file are isolated fixtures, not
future market data. Existing trust/date/conflict refusals remain mandatory.
"""
import io
import json
from http.client import IncompleteRead
import sys
from pathlib import Path
import urllib.error

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import restore_microcap_member_checkpoint as restore
from test_member_split_rebalance_audit import archive, install, SHA, REPO


@pytest.mark.parametrize('stage', ['artifacts', 'run', 'jobs'])
def test_published_checkpoint_recovers_one_transient_api_504(monkeypatch, stage):
    install(monkeypatch, [(10, archive(day='2026-11-09'))])
    original = restore.urllib.request.urlopen
    calls = []
    def open_once_failed(request, **kwargs):
        url = request.full_url
        selected = ('/artifacts?' in url if stage == 'artifacts' else
                    '/jobs?' in url if stage == 'jobs' else
                    '/runs/' in url and '/jobs?' not in url)
        if selected:
            calls.append(url)
            if len(calls) == 1:
                raise urllib.error.HTTPError(url, 504, 'Gateway Time-out', {}, None)
        return original(request, **kwargs)
    monkeypatch.setattr(restore.urllib.request, 'urlopen', open_once_failed)
    monkeypatch.setattr(restore, 'sleep', lambda seconds: None)
    chosen = restore.fetch_latest(REPO, 'isolated', SHA, '2026-11-10')
    assert chosen['id'] == 10
    assert len(calls) == 2 and calls[0] == calls[1]


def test_published_checkpoint_recovers_one_transient_download_504(monkeypatch):
    install(monkeypatch, [(10, archive(day='2026-11-09'))])
    original = restore.download
    calls = []
    def download_once_failed(artifact, token):
        calls.append(artifact['id'])
        if len(calls) == 1:
            raise urllib.error.HTTPError(artifact['archive_download_url'], 504, 'Gateway Time-out', {}, None)
        return original(artifact, token)
    monkeypatch.setattr(restore, 'download', download_once_failed)
    monkeypatch.setattr(restore, 'sleep', lambda seconds: None)
    chosen = restore.fetch_latest(REPO, 'isolated', SHA, '2026-11-10')
    assert chosen['id'] == 10 and calls == [10, 10]


def test_pagination_retries_failed_page_without_restarting_successful_pages(monkeypatch):
    install(monkeypatch, [(10, archive(day='2026-11-09'))])
    original = restore.urllib.request.urlopen
    calls = []
    filler = [dict(id=100+i, name='unrelated', expired=False) for i in range(100)]
    def paged(request, **kwargs):
        url = request.full_url
        if '/artifacts?' in url:
            calls.append(url)
            if url.endswith('page=1'):
                return io.BytesIO(json.dumps({'artifacts': filler}).encode())
            if calls.count(url) == 1:
                raise urllib.error.HTTPError(url, 504, 'Gateway Time-out', {}, None)
        return original(request, **kwargs)
    monkeypatch.setattr(restore.urllib.request, 'urlopen', paged)
    monkeypatch.setattr(restore, 'sleep', lambda seconds: None)
    chosen = restore.fetch_latest(REPO, 'isolated', SHA, '2026-11-10')
    assert chosen['id'] == 10
    assert sum(x.endswith('page=1') for x in calls) == 1
    assert sum(x.endswith('page=2') for x in calls) == 2


@pytest.mark.parametrize('code', [403, 404, 429])
def test_permanent_rejection_does_not_retry_or_select_an_older_checkpoint(monkeypatch, code):
    install(monkeypatch, [(10, archive(day='2026-11-09')), (9, archive(day='2026-11-06'))])
    calls = []
    def deny(request, **kwargs):
        calls.append(request.full_url)
        raise urllib.error.HTTPError(request.full_url, code, 'Rejected', {}, None)
    monkeypatch.setattr(restore.urllib.request, 'urlopen', deny)
    with pytest.raises(urllib.error.HTTPError) as exc:
        restore.fetch_latest(REPO, 'isolated', SHA, '2026-11-10')
    assert exc.value.code == code and len(calls) == 1


def test_exhausted_api_504_does_not_fall_back_to_old_bundled_seed(tmp_path, monkeypatch):
    calls = []
    def fail(request, **kwargs):
        calls.append(request.full_url)
        raise urllib.error.HTTPError(request.full_url, 504, 'Gateway Time-out', {}, None)
    monkeypatch.setattr(restore.urllib.request, 'urlopen', fail)
    monkeypatch.setattr(restore, 'sleep', lambda seconds: None)
    monkeypatch.setattr(restore, 'seed_download', lambda *a: pytest.fail('Unknown published state must not silently become old seed'))
    monkeypatch.setenv('GITHUB_TOKEN', 'isolated')
    monkeypatch.setattr(sys, 'argv', ['restore', '--repository', REPO,
        '--strategy-sha', SHA, '--seed-config', str(tmp_path/'unused.json'),
        '--not-after-date', '2026-11-10', '--out', str(tmp_path/'restored')])
    with pytest.raises(urllib.error.HTTPError): restore.main()
    assert len(calls) == 3
    assert not (tmp_path/'restored').exists()


def test_future_and_same_day_conflicts_are_still_rejected(monkeypatch):
    install(monkeypatch, [(11, archive(day='2026-11-11')), (10, archive(day='2026-11-09'))])
    assert restore.fetch_latest(REPO, 'isolated', SHA, '2026-11-10')['id'] == 10
    install(monkeypatch, [(11, archive(day='2026-11-09', offset=100)), (10, archive(day='2026-11-09'))])
    with pytest.raises(RuntimeError, match='Conflicting'):
        restore.fetch_latest(REPO, 'isolated', SHA, '2026-11-10')


def test_response_body_timeout_reopens_only_the_failed_api_request(monkeypatch):
    install(monkeypatch, [(10, archive(day='2026-11-09'))])
    original = restore.urllib.request.urlopen
    calls = []
    class TimeoutBody(io.BytesIO):
        def read(self, *args, **kwargs):
            raise TimeoutError('isolated response-body timeout')
    def body_timeout_once(request, **kwargs):
        if '/artifacts?' in request.full_url:
            calls.append(request.full_url)
            if len(calls) == 1: return TimeoutBody()
        return original(request, **kwargs)
    monkeypatch.setattr(restore.urllib.request, 'urlopen', body_timeout_once)
    monkeypatch.setattr(restore, 'sleep', lambda seconds: None)
    assert restore.fetch_latest(REPO, 'isolated', SHA, '2026-11-10')['id'] == 10
    assert len(calls) == 2 and calls[0] == calls[1]


@pytest.mark.parametrize('failure', [TimeoutError('isolated timeout'),
    urllib.error.URLError('isolated connection issue'),
    ConnectionResetError('isolated reset'), IncompleteRead(b'partial', 100)])
def test_transient_archive_read_failure_retries_same_artifact(monkeypatch, failure):
    install(monkeypatch, [(10, archive(day='2026-11-09'))])
    original = restore.download
    calls = []
    def fail_once(artifact, token):
        calls.append(artifact['id'])
        if len(calls) == 1: raise failure
        return original(artifact, token)
    monkeypatch.setattr(restore, 'download', fail_once)
    monkeypatch.setattr(restore, 'sleep', lambda seconds: None)
    assert restore.fetch_latest(REPO, 'isolated', SHA, '2026-11-10')['id'] == 10
    assert calls == [10, 10]
