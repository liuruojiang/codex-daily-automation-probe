"""Persistent, diagnostic preparation for local maintenance. No production writes.

The dated market-cap screen is a prioritization input, not native ST, listing,
tradeability, share-lineage or rebalance acceptance. Production stays on its
existing route until those consumers accept the scoped state.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid

URL = 'https://datacenter-web.eastmoney.com/api/data/v1/get'
FIELDS = 'SECURITY_CODE,SECUCODE,TRADE_DATE,TOTAL_SHARES,TOTAL_MARKET_CAP,CLOSE_PRICE'
PAGE_SIZE = 5000
SCHEMA = 'microcap_local_screen_preparation_v1'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf8')


def atomic(path, raw):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temp.write_bytes(raw)
    os.replace(temp, path)


@contextmanager
def writer_lock(path):
    """OS lock survives a crashed writer without stale PID deletion."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def request_params(day, page):
    date.fromisoformat(day)
    return dict(reportName='RPT_VALUEANALYSIS_DET', columns=FIELDS, source='WEB',
                client='WEB', sortColumns='SECURITY_CODE', sortTypes='1',
                pageSize=PAGE_SIZE, pageNumber=page, filter=f"(TRADE_DATE='{day}')")


def decode_page(raw, day):
    result = json.loads(raw)
    if result.get('success') is not True or not isinstance(result.get('result'), dict):
        raise ValueError('Dated market-cap source rejected query')
    payload = result['result']
    count, pages, rows = payload['count'], payload['pages'], payload['data']
    if (type(count) is not int or type(pages) is not int or not 1 <= pages <= 10
            or count < 1 or pages != (count + PAGE_SIZE - 1) // PAGE_SIZE
            or not isinstance(rows, list) or not rows or len(rows) > PAGE_SIZE):
        raise ValueError('Invalid source pagination')
    for row in rows:
        symbol = row['SECURITY_CODE']
        if (not isinstance(symbol, str) or re.fullmatch(r'[0-9]{6}', symbol) is None
                or row['SECUCODE'] not in {symbol + '.' + suffix for suffix in ('SH', 'SZ', 'BJ')}
                or row['TRADE_DATE'] not in {day, day + ' 00:00:00'}):
            raise ValueError('Source symbol/exchange/session differs')
        expected_exchange = ('BJ' if symbol.startswith(('4', '8', '920')) else
                             'SH' if symbol.startswith(('6', '9')) else
                             'SZ' if symbol.startswith(('0', '2', '3')) else None)
        if expected_exchange is None or row['SECUCODE'] != symbol + '.' + expected_exchange:
            raise ValueError('Source exchange does not match security code')
    return payload


def fetch_snapshot(directory, day, get=None):
    """Reuse only hash-bound completed pages; failed-page retries are caller-led."""
    if get is None:
        import requests
        get = requests.get
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    gathered = []
    expected = None
    page = 1
    while True:
        params = request_params(day, page)
        raw_path = directory / f'page-{page:02d}.json'
        receipt_path = directory / f'page-{page:02d}.receipt.json'
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_bytes())
            raw = raw_path.read_bytes()
            if (receipt['url'] != URL or receipt['params'] != params
                    or receipt['sha256'] != sha(raw) or receipt['tls_verify'] is not True):
                raise ValueError('Saved source page binding differs')
        else:
            response = get(URL, params=params, timeout=(10, 40), verify=True)
            response.raise_for_status()
            raw = response.content
            decode_page(raw, day)
            # An interrupted download never overwrites a completed receipt.
            atomic(raw_path, raw)
            atomic(receipt_path, encode(dict(url=URL, params=params, sha256=sha(raw),
                   fetched_at=datetime.now(timezone.utc).isoformat(), tls_verify=True)))
        payload = decode_page(raw, day)
        identity = payload['count'], payload['pages']
        if expected is None:
            expected = identity
        if identity != expected:
            raise ValueError('Source pagination total changed; retain evidence for review')
        if page < payload['pages'] and len(payload['data']) != PAGE_SIZE:
            raise ValueError('Source nonfinal page is incomplete')
        gathered.extend(payload['data'])
        if page == payload['pages']:
            break
        page += 1
    symbols = [row['SECURITY_CODE'] for row in gathered]
    if len(gathered) != expected[0] or len(set(symbols)) != len(symbols):
        raise ValueError('Source coverage has duplicates or missing rows')
    if symbols != sorted(symbols):
        raise ValueError('Source pagination order differs')
    return gathered


def decimal_value(value):
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('Nonnumeric market-cap input') from exc
    if not result.is_finite() or result <= 0:
        raise ValueError('Nonpositive market-cap input')
    return result


def select_pool(rows, members, st_symbols, size=300):
    if size != 300:
        raise ValueError('This preparation contract has exactly 300 slots')
    if (len(members) != 100 or len(set(members)) != 100
            or any(re.fullmatch(r'[0-9]{6}', symbol) is None for symbol in members)):
        raise ValueError('Verified current period must have 100 unique symbols')
    ranked, excluded = [], []
    for row in rows:
        symbol = row['SECURITY_CODE']
        if row['SECUCODE'].endswith('.BJ') or symbol.startswith(('200', '900')):
            excluded.append(dict(symbol=symbol, reason='outside_native_SH_SZ_A_scope'))
            continue
        # Missing/zero source fields are a diagnostic gap, never a zero-cap rank.
        try:
            shares = decimal_value(row['TOTAL_SHARES'])
            close = decimal_value(row['CLOSE_PRICE'])
            cap = decimal_value(row['TOTAL_MARKET_CAP'])
        except ValueError:
            excluded.append(dict(symbol=symbol, reason='source_value_unavailable'))
            continue
        if shares != shares.to_integral_value() or abs(shares * close - cap) > Decimal('0.01'):
            raise ValueError('Total-share units or raw-close cap identity differs: ' + symbol)
        ranked.append((cap, symbol, row))
    if len({s for _, s, _ in ranked}) != len(ranked):
        raise ValueError('Duplicate source symbols')
    ranked.sort(key=lambda item: (item[0], item[1]))
    by_symbol = {symbol: (cap, row) for cap, symbol, row in ranked}
    missing = sorted(set(members) - by_symbol.keys())
    if missing:
        raise ValueError('Current members lack valid dated source rows: ' + ','.join(missing))
    # Existing holdings remain mandatory even if a new risk flag is present.
    chosen = set(members)
    eligible = [(cap, symbol, row) for cap, symbol, row in ranked if symbol not in st_symbols]
    for _, symbol, _ in eligible:
        if len(chosen) >= size:
            break
        chosen.add(symbol)
    if len(chosen) != size:
        raise ValueError('Insufficient screened candidates')
    boundary_missing = sorted({s for _, s, _ in eligible[:100]} - chosen)
    if boundary_missing:
        raise ValueError('Screened top100 escaped preparation pool')
    result = []
    for cap, symbol, row in ranked:
        if symbol in chosen:
            result.append(dict(symbol=symbol, total_market_cap=str(cap),
                               total_shares=str(row['TOTAL_SHARES']),
                               raw_close=str(row['CLOSE_PRICE']),
                               current_member=symbol in members,
                               current_st_reference=symbol in st_symbols))
    return dict(candidates=result, excluded=excluded, source_rows=len(rows),
                screened_eligible=len(eligible), screen_top100_outside_pool=boundary_missing,
                member_count=len(members), candidate_count=len(result))


def protected_state_root(state, strategy):
    state, strategy = Path(state).resolve(), Path(strategy).resolve()
    if state == strategy or state.is_relative_to(strategy) or strategy.is_relative_to(state):
        raise ValueError('Preparation state must be separate from the production checkout')
    return state


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--strategy-root', type=Path, required=True)
    p.add_argument('--state-root', type=Path, required=True)
    p.add_argument('--checkpoint', type=Path, required=True)
    p.add_argument('--st-snapshot', type=Path, required=True)
    p.add_argument('--as-of', required=True)
    args = p.parse_args(argv)
    strategy = args.strategy_root.resolve()
    state = protected_state_root(args.state_root, strategy)
    sys.path.insert(0, str(strategy))
    from scripts.member_period_handoff import verify, read_bound
    from scripts.exchange_calendar import latest_completed_session, is_trading_day
    target = date.fromisoformat(args.as_of)
    if target > latest_completed_session() or not is_trading_day(target):
        raise ValueError('Screen date is not a completed exchange session')
    cp = verify(args.checkpoint)
    if cp['as_of_date'] != args.as_of:
        raise ValueError('Preparation checkpoint differs from dated screen')
    members = [row['symbol'] for row in json.loads(read_bound(cp, 'period'))['effective_members']]
    st_raw = args.st_snapshot.read_bytes()
    st_rows = list(csv.DictReader(st_raw.decode('utf-8-sig').splitlines()))
    st_symbols = {r['code'] for r in st_rows}
    if not st_symbols or any(re.fullmatch(r'[0-9]{6}', s) is None for s in st_symbols):
        raise ValueError('Invalid reference ST snapshot')
    folder = state / 'screens' / args.as_of
    with writer_lock(state / 'preparation.lock'):
        folder.mkdir(parents=True, exist_ok=True)
        run = folder / ('run-' + uuid.uuid4().hex)
        run.mkdir()
        atomic(run / 'st-reference.csv', st_raw)
        try:
            rows = fetch_snapshot(folder / 'source', args.as_of)
            pool = select_pool(rows, members, st_symbols)
            atomic(run / 'candidates.json', encode(pool))
            result = dict(schema=SCHEMA, status='PREPARATION_VERIFIED',
                          production_ready=False, native_maintenance_executed=False,
                          date=args.as_of, checkpoint_sha256=sha(args.checkpoint.read_bytes()),
                          st_reference_sha256=sha(st_raw), st_reference_freshness_certified=False,
                          candidate_count=pool['candidate_count'], member_count=pool['member_count'],
                          source_rows=pool['source_rows'], run_directory=str(run),
                          candidates_sha256=sha((run / 'candidates.json').read_bytes()),
                          remaining_acceptance=['scoped_native_sources_and_consumer_binding',
                              'native_rebalance_parity_and_three_version_delivery',
                              'single_writer_cloud_to_local_cutover', 'missed_run_recovery'])
            atomic(run / 'result.json', encode(result))
            atomic(folder / 'latest.json', encode(result))
        except Exception as exc:
            atomic(run / 'failure.json', encode(dict(status='PREPARATION_FAILED',
                   error_type=type(exc).__name__, error=str(exc), production_ready=False)))
            raise
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
