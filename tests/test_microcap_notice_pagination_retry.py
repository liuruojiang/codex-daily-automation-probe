import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


spec = importlib.util.spec_from_file_location('notice_retry_launcher', Path(__file__).parents[1] / 'scripts/refresh_microcap_notice_retry.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class NoticeError(Exception):
    pass


def test_moving_pages_retry_notice_only_and_keep_evidence(tmp_path):
    calls, phases = [], []
    result = {'complete': True}
    def refresh(**kwargs):
        calls.append(kwargs)
        (tmp_path / f'raw_attempt_{len(calls)}').write_text(str(len(calls)))
        if len(calls) < 3:
            raise NoticeError('Daily notice pagination total changed')
        return result
    assert m.retry_notice_refresh(refresh, NoticeError, phases.append, root=tmp_path, target='2026-11-06') is result
    assert calls == [{'root': tmp_path, 'target': '2026-11-06'}] * 3
    assert len(list(tmp_path.iterdir())) == 3
    assert len(phases) == 2


def test_three_attempt_limit():
    calls = []
    def refresh(**kwargs):
        calls.append(kwargs)
        raise NoticeError('Daily notice pagination total changed')
    with pytest.raises(NoticeError):
        m.retry_notice_refresh(refresh, NoticeError, lambda _: None)
    assert len(calls) == 3


@pytest.mark.parametrize('error', [NoticeError('Daily notice page is incomplete'),
    NoticeError('Daily notice response binding differs'), NoticeError('Historical boundary changed'),
    RuntimeError('Daily notice pagination total changed')])
def test_other_errors_do_not_retry(error):
    calls = []
    def refresh(**kwargs):
        calls.append(kwargs)
        raise error
    with pytest.raises(type(error)) as captured:
        m.retry_notice_refresh(refresh, NoticeError, lambda _: None)
    assert captured.value is error
    assert len(calls) == 1


def test_success_is_not_repeated():
    calls = []
    def refresh(**kwargs):
        calls.append(kwargs)
        return 17
    assert m.retry_notice_refresh(refresh, NoticeError, lambda _: None) == 17
    assert len(calls) == 1


def test_cli_forwards_native_arguments_and_retries_only_notice(tmp_path):
    package = tmp_path / 'scripts'
    package.mkdir()
    (package / '__init__.py').write_text('')
    (package / 'st_notice_evidence.py').write_text('class StNoticeEvidenceError(Exception): pass\n')
    (package / 'bounded_market_data.py').write_text('def fetch_akshare_frame(provider): return provider\n')
    (package / 'st_notice_refresh.py').write_text('''from scripts.st_notice_evidence import StNoticeEvidenceError
calls=0
def refresh_notice_archive(**kwargs):
    global calls
    calls+=1
    if calls<2: raise StNoticeEvidenceError("Daily notice pagination total changed")
    return kwargs
''')
    (package / 'realtime_state_bundle.py').write_text('''from scripts import st_notice_refresh as n
def _log_refresh_phase(value): print(value)
def main(argv):
    assert argv[0]=="refresh" and argv[-2:]==["--max-workers","4"]
    assert n.refresh_notice_archive(target="2026-11-06")=={"target":"2026-11-06"}
    assert n.calls==2
    print("native_cli_completed_once")
    return 0
''')
    before = {p.name:p.read_bytes() for p in package.iterdir()}
    result = subprocess.run([sys.executable, '-B', str(Path(m.__file__)), 'refresh', '--root', str(tmp_path), '--max-workers', '4'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.count('native_cli_completed_once') == 1
    assert 'retry_notice_pagination_1_of_2' in result.stdout
    assert {p.name:p.read_bytes() for p in package.iterdir()} == before


def test_universe_deadline_retries_only_failed_provider_once():
    calls, phases = [], []
    result = object()
    def fetch(provider, **kwargs):
        calls.append((provider, kwargs))
        if len(calls) == 1:
            raise TimeoutError('stock_zh_a_spot_em exceeded its 90s total deadline')
        return result
    assert m.retry_universe_timeout(fetch, phases.append, 'stock_zh_a_spot_em', timeout=90) is result
    assert calls == [('stock_zh_a_spot_em', {'timeout': 90})] * 2
    assert phases == ['retry_universe_timeout_stock_zh_a_spot_em_1_of_1']


def test_universe_timeout_limit():
    calls = []
    def fetch(provider):
        calls.append(provider)
        raise TimeoutError('deadline exceeded')
    with pytest.raises(TimeoutError):
        m.retry_universe_timeout(fetch, lambda _: None, 'stock_zh_a_spot_em')
    assert len(calls) == 2


@pytest.mark.parametrize('provider,error', [('stock_zh_a_st_em', TimeoutError('deadline')), ('stock_zh_a_spot_em', ValueError('invalid table')), ('stock_zh_a_spot_em', RuntimeError('HTTP 403'))])
def test_universe_does_not_retry_other_failures(provider, error):
    calls = []
    def fetch(provider):
        calls.append(provider)
        raise error
    with pytest.raises(type(error)):
        m.retry_universe_timeout(fetch, lambda _: None, provider)
    assert len(calls) == 1
