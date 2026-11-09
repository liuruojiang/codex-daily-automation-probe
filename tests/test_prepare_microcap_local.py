"""Scope, real field units, crash recovery and rejection invariants."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_microcap_local as prep


def row(n, **changes):
    symbol = f'{n:06d}'
    return dict(SECURITY_CODE=symbol, SECUCODE=symbol + '.SZ',
                TRADE_DATE='2026-11-06 00:00:00', TOTAL_SHARES=100000000,
                CLOSE_PRICE=n, TOTAL_MARKET_CAP=n * 100000000, **changes)


def test_pool_retains_holdings_and_small_cap_boundary_with_st_reference():
    data = [row(n) for n in range(1, 501)]
    members = [f'{n:06d}' for n in range(401, 501)]
    st = {f'{n:06d}' for n in range(1, 51)}
    result = prep.select_pool(data, members, st)
    symbols = {r['symbol'] for r in result['candidates']}
    assert len(symbols) == 300
    assert set(members) <= symbols
    assert {f'{n:06d}' for n in range(51, 151)} <= symbols
    assert not symbols & st


@pytest.mark.parametrize('field,value', [('TOTAL_SHARES', 10000.1),
                                        ('TOTAL_MARKET_CAP', 1),
                                        ('CLOSE_PRICE', 'NaN')])
def test_member_invalid_source_cannot_succeed(field, value):
    data = [row(n) for n in range(1, 401)]
    data[0][field] = value
    with pytest.raises(ValueError):
        prep.select_pool(data, [f'{n:06d}' for n in range(1, 101)], set())


def test_bj_is_not_admitted_and_current_st_holding_is_retained():
    data = [row(n) for n in range(1, 401)]
    bj = row(430001)
    bj['SECUCODE'] = '430001.BJ'
    result = prep.select_pool(data + [bj], [f'{n:06d}' for n in range(1, 101)], {'000001'})
    assert '430001' not in {r['symbol'] for r in result['candidates']}
    assert next(r for r in result['candidates'] if r['symbol'] == '000001')['current_st_reference']


def page(data, count=5001, pages=2):
    return prep.encode(dict(success=True, result=dict(data=data, count=count, pages=pages)))


class Response:
    def __init__(self, raw):
        self.content = raw
    def raise_for_status(self):
        pass


def test_interrupted_second_page_resumes_only_missing_page(tmp_path):
    first = page([row(n) for n in range(1, 5001)])
    last = page([row(5001)])
    calls = []
    def failing(url, **kwargs):
        calls.append(kwargs['params']['pageNumber'])
        if calls[-1] == 2:
            raise TimeoutError('bounded source request failed')
        return Response(first)
    with pytest.raises(TimeoutError):
        prep.fetch_snapshot(tmp_path, '2026-11-06', failing)
    assert calls == [1, 2]
    assert (tmp_path / 'page-01.receipt.json').is_file()
    def recover(url, **kwargs):
        calls.append(kwargs['params']['pageNumber'])
        assert kwargs['verify'] is True
        return Response(last)
    rows = prep.fetch_snapshot(tmp_path, '2026-11-06', recover)
    assert len(rows) == 5001 and calls == [1, 2, 2]
    (tmp_path / 'page-01.json').write_bytes(first + b' ')
    with pytest.raises(ValueError, match='binding'):
        prep.fetch_snapshot(tmp_path, '2026-11-06', recover)
    assert calls == [1, 2, 2]


@pytest.mark.parametrize('change', ['date', 'exchange', 'exchange_suffix', 'duplicate', 'total'])
def test_bad_snapshot_never_becomes_complete(tmp_path, change):
    data = [row(1), row(2)]
    if change == 'date': data[0]['TRADE_DATE'] = '2026-11-09 00:00:00'
    if change == 'exchange': data[0]['SECUCODE'] = '000002.SZ'
    if change == 'exchange_suffix': data[0]['SECUCODE'] = '000001.SH'
    if change == 'duplicate': data[1] = data[0]
    count = 3 if change == 'total' else 2
    with pytest.raises(ValueError):
        prep.fetch_snapshot(tmp_path, '2026-11-06',
                            lambda *a, **kw: Response(page(data, count=count, pages=1)))


def test_native_production_directory_cannot_be_preparation_state(tmp_path):
    strategy = tmp_path / 'strategy'
    for path in (strategy, strategy / 'outputs', tmp_path):
        with pytest.raises(ValueError):
            prep.protected_state_root(path, strategy)
    assert prep.protected_state_root(tmp_path / 'separate', strategy) == tmp_path / 'separate'


def test_single_writer_lock_released_after_failure(tmp_path):
    lock = tmp_path / 'writer.lock'
    with pytest.raises(RuntimeError):
        with prep.writer_lock(lock):
            with pytest.raises(OSError):
                with prep.writer_lock(lock):
                    pytest.fail('another writer acquired held lock')
            raise RuntimeError('crashed stage')
    with prep.writer_lock(lock):
        pass
