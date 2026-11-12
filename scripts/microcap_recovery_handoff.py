"""Strict Auto-controlled recovery from a formal native latest-close export.

This module never refreshes markets, changes native code, or sends anything.
Native refresh-all/check remain prerequisites owned by the coordinator.  The
transport proof is independently checked before cloud or local adoption.
"""
from __future__ import annotations

import argparse
import base64
import copy
import csv
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import importlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import zlib

MODE = 'native_formal_gap_recovery_v1'
KEY = 'native_recovery_v1'
SCHEMA = 'member_formal_gap_recovery_v1'
BOOTSTRAP = 'recovery_formal_exports_v1'
CONTROLLER = 'auto_formal_gap_recovery_20261112_v1'
NATIVE = 'a7c1b9ac33454be4367880a0989dfc1466640503'
M8 = '95d896fe58cc7cb84c0fcb7bd76534f62c9430f4'
REVIEWED_NATIVES = frozenset({NATIVE, M8})
LEGACY_M7_CONTROLLER_SHA = 'eea88c4886baaba42256a4b57050432a0e3a4e4dfe12e827f99086d3b0389b16'
CORE = 'ad4de441d35442616f91aa613146ffdf8cd479f3'
BRIDGE = 'hedge_precision_bridge_v1'
VERSIONS = ('v2.0', 'v2.3', 'v2.5')
REVISIONS = {'v2.0': 'plain_mom16_fixed1_20260904', 'v2.3': 'plain_lb25_hl2p5_r2off_no_overheat_20261029',
             'v2.5': 'plain_lb20_hl3_entry0_exit0_20260905'}
ROLES = ('period', 'history', 'turnover', 'version_state', 'prices')
MAX_BLOB = 8 * 1024 * 1024
MAX_PROOF = 16 * 1024 * 1024
PREFIX = 'microcap_top100_mom16_biweekly_live_v2_0_base'
PANEL = 'outputs/' + PREFIX + '_panel_refreshed.csv'
EXPORTS = {
    'proxy': 'outputs/wind_microcap_top_100_biweekly_thursday_16y_cached.csv',
    'panel': PANEL,
    'effective': 'outputs/' + PREFIX + '_proxy_effective_members.csv',
    'members': 'outputs/' + PREFIX + '_proxy_members.csv',
    'turnover': 'outputs/' + PREFIX + '_proxy_turnover.csv',
    **{v: 'outputs/microcap_top100_mom16_biweekly_live_' + v.replace('.', '_') + '_latest_signal.csv' for v in VERSIONS},
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def _hash(value):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise ValueError('Recovery needs an exact SHA256')
    return value


def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError('Recovery needs canonical exchange dates')
    return value


def _number(value):
    if isinstance(value, bool):
        raise ValueError('Recovery history has an invalid number')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError('Recovery history has an invalid number') from exc
    if not result.is_finite() or result <= 0:
        raise ValueError('Recovery history has an invalid number')
    return result


def _blob(raw):
    if len(raw) > MAX_BLOB:
        raise ValueError('Recovery original object exceeds its budget')
    return dict(sha256=sha(raw), encoding='zlib+base64', base64=base64.b64encode(zlib.compress(raw)).decode('ascii'))


def _unblob(value):
    if not isinstance(value, dict) or set(value) not in ({'sha256', 'base64'}, {'sha256', 'base64', 'encoding'}):
        raise ValueError('Recovery original object is unbound')
    text = value['base64']
    if not isinstance(text, str) or len(text) > MAX_PROOF:
        raise ValueError('Recovery original object exceeds its budget')
    raw = base64.b64decode(text, validate=True)
    if value.get('encoding') == 'zlib+base64':
        decoder = zlib.decompressobj()
        raw = decoder.decompress(raw, MAX_BLOB + 1)
        if not decoder.eof or decoder.unconsumed_tail or decoder.unused_data:
            raise ValueError('Recovery compressed object is incomplete or exceeds its budget')
    elif 'encoding' in value:
        raise ValueError('Unknown recovery object encoding')
    if len(raw) > MAX_BLOB or sha(raw) != _hash(value['sha256']):
        raise ValueError('Recovery original object bytes changed')
    return raw


def _rows(raw):
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
    if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
        raise ValueError('Recovery CSV columns are ambiguous')
    result = list(reader)
    if any(None in row or any(v is None for v in row.values()) for row in result):
        raise ValueError('Recovery CSV row width differs')
    return result


def _history(raw):
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
    if reader.fieldnames != ['date', 'microcap', 'hedge']:
        raise ValueError('Recovery history columns differ')
    result = list(reader)
    days = [_day(row['date']) for row in result]
    if not days or days != sorted(set(days)):
        raise ValueError('Recovery history has missing/ambiguous order')
    for row in result:
        _number(row['microcap']); _number(row['hedge'])
    return result


def _bound(document, payload):
    if set(payload) != set(ROLES):
        raise ValueError('Recovery checkpoint payload closure differs')
    if document.get('schema') != 'member_daily_checkpoint_v1':
        raise ValueError('Recovery checkpoint schema differs')
    _day(document['as_of_date'])
    for role in ROLES:
        spec = document[role]
        if (not isinstance(spec.get('path'), str) or Path(spec['path']).name != spec['path']
                or any(c in spec['path'] for c in ('/', '\\', ':')) or spec['path'].endswith(('.', ' '))):
            raise ValueError('Recovery dependency has another path role')
        if sha(payload[role]) != spec['sha256']:
            raise ValueError('Recovery checkpoint original dependency changed: ' + role)
    history = _history(payload['history'])
    if history[-1]['date'] != document['as_of_date']:
        raise ValueError('Recovery checkpoint/history date differs')
    return json.loads(payload['period']), history


def _package(document, payload):
    period, history = _bound(document, payload)
    members = period.get('effective_members', [])
    symbols = [row.get('symbol') for row in members]
    if (period.get('schema') != 'member_period_v1' or len(symbols) != 100 or len(set(symbols)) != 100
            or any(re.fullmatch('[0-9]{6}', s or '') is None for s in symbols)
            or [int(row['rank']) for row in members] != list(range(1, 101))
            or any(_number(row.get('target_weight', '.01')) != Decimal('.01') for row in members)):
        raise ValueError('Recovery requires the original equally weighted effective 100')
    states = json.loads(payload['version_state'])['versions']
    if (set(states) != set(VERSIONS) or any(str(row.get('date', ''))[:10] != document['as_of_date'] for row in states.values())
            or any(row.get('strategy_revision') != REVISIONS[v] for v, row in states.items())):
        raise ValueError('Recovery three-version state dates differ')
    basis = json.loads(payload['prices'])
    if (basis.get('schema') != 'member_price_basis_v1' or basis.get('as_of_date') != document['as_of_date']
            or sorted(row['symbol'] for row in basis['rows']) != sorted(symbols)
            or any(row['previous_raw_date'] != document['as_of_date'] for row in basis['rows'])):
        raise ValueError('Recovery price basis is not its actual effective basket/date')
    for row in basis['rows']:
        _number(row['previous_raw_close'])
    return period, history, states


def _native(root):
    root = Path(root).resolve()
    sys.path.insert(0, str(root))
    handoff = importlib.import_module('scripts.member_period_handoff')
    daily = importlib.import_module('scripts.member_daily_signal')
    calendar = importlib.import_module('scripts.exchange_calendar')
    base = importlib.import_module('microcap_top100_mom16_biweekly_live_v2_0').base_mod
    precision = importlib.import_module('scripts.member_hedge_precision_bridge')
    return handoff, daily, calendar, base, precision


def _runtime(root):
    value = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True, encoding='utf-8').strip()
    if value not in REVIEWED_NATIVES:
        raise ValueError('Recovery requires an exact reviewed native runtime')
    return value


def _calendar(root, target):
    _, _, calendar, base, _ = _native(root)
    import pandas as pd
    values = sorted({_day(str(d.date() if hasattr(d, 'date') else d)) for d in calendar.sessions_for_day(date.fromisoformat(target))})
    dates = pd.DatetimeIndex(values)
    rebalances = list(base.build_biweekly_rebalance_dates(dates).strftime('%Y-%m-%d'))
    if values != sorted(set(values)) or target not in values:
        raise ValueError('Independent calendar does not cover recovery target')
    if rebalances != _rebalances(values):
        raise ValueError('Native biweekly calendar differs from reviewed recovery contract')
    return values


def _latest(root, target):
    _, _, calendar, _, _ = _native(root)
    if calendar.latest_completed_session().isoformat() != target:
        raise ValueError('Recovery computation must use the actual latest completed exchange session')


def _rebalances(values):
    # This is only a transport recheck of the unchanged native W-WED grouping.
    # The builder also calls the actual native calendar implementation above.
    anchor = date(2016, 1, 7)
    groups = {}
    for value in values:
        day = date.fromisoformat(_day(value)); week = (day - anchor).days // 7
        if week % 2 == 0:
            groups.setdefault(week, value)
    return [groups[k] for k in sorted(groups)]


def _semantic_events(raw):
    date_keys = {'rebalance_date', 'constraint_trade_date', 'execution_date', 'effective_date', 'return_start_date'}
    result = []
    for row in _rows(raw):
        normalized = {}
        for key, value in row.items():
            if key in date_keys:
                normalized[key] = _day(value[:10]) if value else None
            else:
                try:
                    normalized[key] = Decimal(value) if value else None
                except InvalidOperation:
                    normalized[key] = value
        result.append(normalized)
    return result


def _preserved_turnover(old_events, new_events, parent_day, calendar):
    prefix = [copy.deepcopy(row) for row in new_events if row['rebalance_date'] <= parent_day]
    old = copy.deepcopy(old_events)
    if len(old) != len(prefix):
        raise ValueError('Recovery changed already earned turnover/cost events')
    for previous, current in zip(old, prefix):
        if previous.get('return_start_date') is None and current.get('return_start_date') is not None:
            execution = previous.get('execution_date')
            actual_next = next((day for day in calendar if day > execution), None)
            if actual_next != current['return_start_date']:
                raise ValueError('Recovery assigned a false first-return session')
            previous['return_start_date'] = actual_next
        if previous != current:
            raise ValueError('Recovery changed already earned turnover/cost events')


def _precision_record(record):
    """Portable recheck of the original exact EM/Sina source contract."""
    if not isinstance(record, dict) or set(record) != {'date', 'earned', 'formal'}:
        raise ValueError('Recovery precision witness schema differs')
    day = _day(record['date']); earned, formal = record['earned'], record['formal']
    if (earned.get('provider') != 'eastmoney' or earned.get('instrument') != '1.000852'
            or formal.get('provider') != 'sina_legacy' or formal.get('instrument') != 'sh000852'):
        raise ValueError('Recovery precision provider/instrument differs')
    quote = json.loads(_unblob(earned['hedge_json']), parse_float=Decimal)
    attempts = quote.get('source_attempts', [])
    stamp = datetime.fromisoformat(quote['collected_at'])
    from zoneinfo import ZoneInfo
    local = stamp.astimezone(ZoneInfo('Asia/Shanghai'))
    if (quote.get('source') != 'eastmoney' or quote.get('market_date') != day or quote.get('close_confirmed') is not True
            or not attempts or attempts[-1].get('source') != 'eastmoney' or attempts[-1].get('ok') is not True
            or stamp.tzinfo is None or local.date().isoformat() != day or (local.hour, local.minute) < (15, 0)):
        raise ValueError('Recovery precision earned source clock/receipt differs')
    bars = _rows(_unblob(earned['bar_csv']))
    old, new = _number(quote['close']), _number(formal['close'])
    if len(bars) != 1 or bars[0]['date'] != day or _number(bars[0]['close']) != old:
        raise ValueError('Recovery precision earned source bar differs')
    if (old == new or old.quantize(Decimal('.01')) != old or new.quantize(Decimal('.001')) != new
            or new.quantize(Decimal('.01'), rounding=ROUND_HALF_UP) != old):
        raise ValueError('Recovery precision does not satisfy exact quantization')
    _hash(formal['manifest_sha256']); _hash(formal['panel_sha256'])
    text = _unblob(formal['source_log']).decode('utf-8-sig')
    try:
        docs = [json.loads(text)]
    except ValueError:
        docs = [json.loads(line.split('[index-history] ', 1)[1]) for line in text.splitlines() if '[index-history] ' in line]
    if not any(isinstance(doc, dict) and doc.get('independent_history_source') == 'sina_legacy'
               and doc.get('independent_history_start', '') <= day <= doc.get('independent_history_end', '')
               and doc.get('independent_history_attempts') and doc['independent_history_attempts'][-1].get('source') == 'sina_legacy'
               and doc['independent_history_attempts'][-1].get('ok') is True for doc in docs):
        raise ValueError('Recovery precision lacks its actual formal source receipt')
    return day, old, new


def _compare_prefix(old, new, records):
    if len(new) < len(old):
        raise ValueError('Recovery cannot remove earned sessions')
    used = set()
    for earned, formal in zip(old, new):
        if earned['date'] != formal['date'] or _number(earned['microcap']) != _number(formal['microcap']):
            raise ValueError('Recovery cannot revise earned microcap/date history')
        if _number(earned['hedge']) == _number(formal['hedge']):
            continue
        record = records.get(earned['date'])
        if record is None:
            raise ValueError('Recovery hedge difference lacks its exact original witness')
        day, old_value, new_value = _precision_record(record)
        if old_value != _number(earned['hedge']) or new_value != _number(formal['hedge']):
            raise ValueError('Recovery precision witness values differ')
        used.add(day)
    return used


def _append(raw, rows):
    newline = '\r\n' if b'\r\n' in raw else '\n'
    stream = io.StringIO(newline='')
    csv.DictWriter(stream, fieldnames=['date', 'microcap', 'hedge'], lineterminator=newline).writerows(rows)
    return raw + (b'' if raw.endswith((b'\r', b'\n')) else newline.encode()) + stream.getvalue().encode('utf-8')


def _read_package(path, handoff):
    document = handoff.verify(path)
    payload = {role: handoff.read_bound(document, role) for role in ROLES}
    document = {k: v for k, v in document.items() if k != '_directory'}
    return document, payload


def _formal(root, target):
    manifest_raw = (root / 'outputs/top100_delivery_manifest.json').read_bytes()
    manifest = json.loads(manifest_raw)
    if (manifest.get('ok') is not True or manifest.get('status') != 'complete'
            or manifest.get('scope') != 'whole_workspace_delivery' or manifest.get('expected_date') != target):
        raise ValueError('Recovery requires the complete native latest-close formal manifest')
    if manifest.get('release_sha') != _runtime(root):
        raise ValueError('Recovery formal native release differs')
    hashes = {**manifest['inputs'], **manifest['artifacts']}
    for name, expected in hashes.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise ValueError('Formal manifest source escapes native root')
        raw = path.read_bytes()
        if sha(raw) != expected and (name.startswith('data/st_notice_evidence/') or sha(raw.replace(b'\r\n', b'\n')) != expected):
            raise ValueError('Recovery formal source changed: ' + name)
    exports = {}
    for name in EXPORTS.values():
        raw = (root / name).read_bytes()
        if name not in hashes or hashes[name] not in (sha(raw), sha(raw.replace(b'\r\n', b'\n'))):
            raise ValueError('Recovery export not bound by whole formal manifest: ' + name)
        exports[name] = raw
    return manifest_raw, exports


def bootstrap_formal_candidate(root, target, out):
    """Read a completed D export while retaining its actual last-R period."""
    root = Path(root).resolve(); out = Path(out).resolve(); target = _day(target)
    runtime = _runtime(root)
    _latest(root, target)
    h, _, _, _, _ = _native(root)
    manifest_raw, exports = _formal(root, target)
    calendar = _calendar(root, target)
    turnover = _rows(exports[EXPORTS['turnover']])
    member_rows = _rows(exports[EXPORTS['members']])
    anchors = sorted({_day(row['rebalance_date'][:10]) for row in member_rows if row['rebalance_date'][:10] <= target})
    if not anchors:
        raise ValueError('Recovery formal members have no original period anchor')
    anchor = anchors[-1]
    if anchor not in _rebalances(calendar):
        raise ValueError('Recovery formal period anchor is not an original rebalance')
    targets = [row for row in member_rows if row['rebalance_date'][:10] == anchor]
    if len(targets) != 100 or len({row['symbol'] for row in targets}) != 100:
        raise ValueError('Recovery formal target members incomplete')
    effective = sorted(_rows(exports[EXPORTS['effective']]), key=lambda row: int(row['rank']))
    if len(effective) != 100 or any(row['as_of_date'][:10] > target for row in effective):
        raise ValueError('Recovery current executed-member snapshot differs')
    names = {row['symbol'].zfill(6): row.get('name', '') for row in member_rows}
    members, prices, source_files = [], [], {name: sha(raw) for name, raw in exports.items()}
    for row in effective:
        symbol = row['symbol'].zfill(6)
        relative = '.microcap_index_cache/prices_raw/' + symbol + '.csv'
        raw = (root / relative).read_bytes(); source_files[relative] = sha(raw)
        closes = [bar for bar in _rows(raw) if bar['date'][:10] == target]
        if len(closes) != 1:
            raise ValueError('Recovery member raw close absent at latest target: ' + symbol)
        members.append(dict(rank=int(row['rank']), symbol=symbol, name=names.get(symbol, ''), target_weight=.01))
        prices.append(dict(symbol=symbol, previous_raw_date=target, previous_raw_close=float(_number(closes[0]['close_raw']))))
    proxy = _rows(exports[EXPORTS['proxy']]); panel = {row['date'][:10]: row for row in _rows(exports[EXPORTS['panel']])}
    if proxy[-1]['date'][:10] != target:
        raise ValueError('Recovery formal proxy does not reach D')
    history = io.StringIO(newline=''); writer = csv.DictWriter(history, fieldnames=['date', 'microcap', 'hedge']); writer.writeheader()
    for row in proxy:
        day = row['date'][:10]
        writer.writerow(dict(date=day, microcap=row['close'], hedge=panel[day]['1.000852']))
    states = {}
    for version in VERSIONS:
        values = _rows(exports[EXPORTS[version]])
        if len(values) != 1 or values[0]['date'][:10] != target:
            raise ValueError('Recovery formal version state does not reach D')
        states[version] = values[0]
    next_trade = next((day for day in calendar if day > target), None)
    next_r = next((day for day in _rebalances(calendar) if day > target), None)
    effective_from = next((day for day in calendar if day > anchor), None)
    if not next_trade or not next_r or not effective_from:
        raise ValueError('Recovery independent calendar lacks next session or period')
    events = [copy.deepcopy(row) for row in turnover if row['rebalance_date'][:10] == anchor]
    if not events:
        raise ValueError('Recovery formal period has no original close event')
    for event in events:
        if event.get('execution_timing') != 'close' or event.get('execution_date', '')[:10] != anchor:
            raise ValueError('Recovery formal event changed the close execution')
        if not event.get('return_start_date'):
            event.update(derived_return_start_date=effective_from, return_start_date_source='independent_calendar_next_session_after_native_close')
    period = dict(schema='member_period_v1', package_id=sha(encode(dict(seed_date=anchor, members=members, events=events))),
        anchor_day=anchor, member_as_of=anchor, next_rebalance_date=next_r, effective_from=effective_from,
        effective_members=members, target_members=targets, rebalance_events=events,
        source_files=source_files, source_manifest_sha256=sha(manifest_raw), transport_line_endings_lf_equivalent=[])
    _latest(root, target)
    result = h._write_package(out, {
        'period': ('period.json', encode(period)), 'history': ('history.csv', history.getvalue().encode()),
        'turnover': ('turnover.csv', exports[EXPORTS['turnover']]),
        'version_state': ('version_state.json', encode(dict(schema='member_version_state_v1', versions=states))),
        'prices': ('member_prices.json', encode(dict(schema='member_price_basis_v1', as_of_date=target, rows=prices))),
    }, dict(as_of_date=target, next_trade_date=next_trade, strategy_sha=runtime, bootstrap_source=BOOTSTRAP,
            bootstrap_controller=CONTROLLER))
    return result


def reviewed_controller_identity(checkpoint):
    """Bind each native reader to the controller that could actually earn it."""
    runtime=checkpoint.get('strategy_sha')
    identity=checkpoint.get('recovery_controller',{})
    current=sha(Path(__file__).read_bytes().replace(b'\r\n',b'\n'))
    allowed={current}
    if runtime==NATIVE:
        # Keep earned M7 packages readable after the source-only reader update.
        # This old controller never produced M8 and cannot be used to claim it.
        allowed.add(LEGACY_M7_CONTROLLER_SHA)
    if (runtime not in REVIEWED_NATIVES or checkpoint.get('handoff_mode')!=MODE
            or not isinstance(identity,dict) or set(identity)!={'revision','source_sha256'}
            or identity.get('revision')!=CONTROLLER or identity.get('source_sha256') not in allowed):
        raise ValueError('Unreviewed recovery controller source or native relationship')
    return dict(identity)


def validate_recovery_payload(checkpoint, payload_bytes):
    """Pure transport validation; native calculation is a separate build gate.

    Daily descendants keep the original proof and immutable recovered prefix.
    Original ancestor proof blobs are hash-bound, budgeted objects, never
    recursively interpreted as a new adoption instruction.
    """
    period = json.loads(payload_bytes['period'])
    proof = period.get(KEY)
    if proof is None:
        if checkpoint.get('handoff_mode') == MODE or 'recovery_controller' in checkpoint:
            raise ValueError('Recovery checkpoint lacks its proof')
        return None
    if len(encode(proof)) > MAX_PROOF or proof.get('schema') != SCHEMA or proof.get('controller') != CONTROLLER:
        raise ValueError('Recovery proof contract or budget differs')
    runtime=checkpoint.get('strategy_sha')
    if checkpoint.get('handoff_mode') != MODE or runtime not in REVIEWED_NATIVES:
        raise ValueError('Recovery producer/mode differs')
    controller = reviewed_controller_identity(checkpoint)
    if controller != proof.get('controller_identity') or controller.get('revision') != CONTROLLER:
        raise ValueError('Recovery controller identity differs')
    target = _day(proof['target']); parent = proof['parent']; formal = proof['formal']
    stamp = datetime.fromisoformat(proof['verified_at'])
    now = datetime.now(timezone.utc)
    close = datetime.fromisoformat(target + 'T15:00:00+08:00')
    current_close = datetime.fromisoformat(_day(checkpoint['as_of_date']) + 'T15:00:00+08:00')
    if stamp.tzinfo is None or not close <= stamp <= now or current_close > now:
        raise ValueError('Recovery source clock is future or predates its actual close')
    if proof.get('historical_rewrite') is not False or proof.get('SMTP') is not False:
        raise ValueError('Recovery proof does not preserve no-rewrite/no-send boundaries')
    current_doc = json.loads(_unblob(parent['checkpoint']))
    candidate = json.loads(_unblob(formal['checkpoint']))
    old_payload = {key: _unblob(parent[key]) for key in ROLES}
    formal_payload = {key: _unblob(formal[key]) for key in ROLES}
    old_period, old_rows, _ = _package(current_doc, old_payload)
    new_period, new_rows, formal_states = _package(candidate, formal_payload)
    _, actual_rows, actual_states = _package(checkpoint, payload_bytes)
    if current_doc.get('period_status', 'current') != 'current' or current_doc['as_of_date'] >= target:
        raise ValueError('Recovery parent must be an earlier confirmed current checkpoint')
    if old_period.get('next_rebalance_date', '') <= current_doc['as_of_date']:
        raise ValueError('Recovery parent is an overdue reference period')
    if current_doc.get('strategy_sha') not in (CORE, *REVIEWED_NATIVES, '1634a27c5a609a46ab35fc29ca2c65b94182671b', '14e9a9fa6f0487f1d5547598435089c43f2f9fd1'):
        raise ValueError('Recovery parent producer differs')
    if candidate.get('strategy_sha') != runtime or candidate.get('bootstrap_source') not in (BOOTSTRAP, 'existing_formal_exports') or candidate['as_of_date'] != target:
        raise ValueError('Recovery formal candidate identity differs')
    if candidate.get('bootstrap_source') == BOOTSTRAP and candidate.get('bootstrap_controller') != CONTROLLER:
        raise ValueError('Recovery formal bootstrap controller differs')
    if checkpoint.get('handoff_from') != old_period['package_id']:
        raise ValueError('Recovery parent period lineage differs')
    calendar = proof['calendar_sessions']
    if not isinstance(calendar, list) or calendar != sorted(set(calendar)) or any(_day(d) != d for d in calendar):
        raise ValueError('Recovery calendar is ambiguous')
    suffix = [day for day in calendar if current_doc['as_of_date'] < day <= target]
    if not suffix or suffix != [row['date'] for row in new_rows[len(old_rows):]] or current_doc['next_trade_date'] != suffix[0]:
        raise ValueError('Recovery cannot skip or invent an exchange session')
    rebalances = [day for day in _rebalances(calendar) if current_doc['as_of_date'] < day <= target]
    if proof['sessions'] != suffix or proof['rebalance_sessions'] != rebalances:
        raise ValueError('Recovery native session/period order differs')
    descendant_days = [day for day in calendar if target < day <= checkpoint['as_of_date']]
    if (checkpoint['as_of_date'] not in calendar
            or descendant_days != [row['date'] for row in actual_rows[len(new_rows):]]
            or checkpoint.get('next_trade_date') != next((day for day in calendar if day > checkpoint['as_of_date']), None)):
        raise ValueError('Recovery daily descendant skips a session or has a false next-session clock')
    expected_phase = 'awaiting_handoff' if period['next_rebalance_date'] <= checkpoint['as_of_date'] else 'current'
    if checkpoint.get('period_status', 'current') != expected_phase:
        raise ValueError('Recovery daily descendant has a false current/reference phase')
    if rebalances and (old_period['anchor_day'] >= rebalances[0] or new_period['anchor_day'] != rebalances[-1]):
        raise ValueError('Recovery does not cover every missing native period')
    if not rebalances and any(new_period.get(key) != old_period.get(key) for key in ('anchor_day', 'effective_from', 'effective_members', 'target_members', 'rebalance_events')):
        raise ValueError('No-rebalance recovery changed its executed period')
    records = {row['date']: row for row in proof['precision_records']}
    if len(records) != len(proof['precision_records']):
        raise ValueError('Recovery precision witness duplicate')
    used = _compare_prefix(old_rows, new_rows, records)
    if sorted(used) != proof['used_precision_dates']:
        raise ValueError('Recovery precision witness use differs')
    derived = _append(old_payload['history'], new_rows[len(old_rows):])
    if (sha(derived) != proof['recovered_history_sha256'] or proof['recovered_history_bytes'] != len(derived)
            or not payload_bytes['history'].startswith(derived) or checkpoint['as_of_date'] < target):
        raise ValueError('Recovery changed its earned history prefix')
    if [row['date'] for row in actual_rows[:len(new_rows)]] != [row['date'] for row in new_rows]:
        raise ValueError('Recovery descendant removed recovered sessions')
    if period.get(BRIDGE) != old_period.get(BRIDGE):
        raise ValueError('Recovery discarded or rewrote its original precision bridge')
    expected_period = {key: value for key, value in new_period.items() if key != BRIDGE}
    actual_period = {key: value for key, value in period.items() if key not in (KEY, BRIDGE)}
    if actual_period != expected_period:
        raise ValueError('Recovery changed the formal executed-member period')
    old_events, new_events = _semantic_events(old_payload['turnover']), _semantic_events(formal_payload['turnover'])
    old_day = current_doc['as_of_date']
    _preserved_turnover(old_events, new_events, old_day, calendar)
    actual_new_r = sorted({row['rebalance_date'] for row in new_events if old_day < row['rebalance_date'] <= target})
    if actual_new_r != rebalances or any(row['rebalance_date'] > target for row in new_events):
        raise ValueError('Recovery formal turnover omits a native period')
    if payload_bytes['turnover'] != formal_payload['turnover']:
        raise ValueError('Recovery current turnover differs from complete formal candidate')
    manifest_raw = _unblob(formal['manifest']); manifest = json.loads(manifest_raw)
    if (manifest.get('ok') is not True or manifest.get('status') != 'complete'
            or manifest.get('scope') != 'whole_workspace_delivery' or manifest.get('expected_date') != target or manifest.get('release_sha') != runtime
            or new_period['source_manifest_sha256'] != sha(manifest_raw)):
        raise ValueError('Recovery formal whole-delivery proof differs')
    hashes = {**manifest['inputs'], **manifest['artifacts']}
    exports = {name: _unblob(spec) for name, spec in formal['exports'].items()}
    if set(exports) != set(EXPORTS.values()):
        raise ValueError('Recovery formal export closure differs')
    for name, raw in exports.items():
        if (new_period['source_files'].get(name) != sha(raw)
                or hashes.get(name) not in (sha(raw), sha(raw.replace(b'\r\n', b'\n')))):
            raise ValueError('Recovery formal export source binding differs')
    member_rows = _rows(exports[EXPORTS['members']])
    anchors = sorted({_day(row['rebalance_date'][:10]) for row in member_rows if row['rebalance_date'][:10] <= target})
    if not anchors:
        raise ValueError('Recovery formal member source lacks its last real period')
    last_r = anchors[-1]
    future = next((day for day in calendar if day > target), None)
    future_r = next((day for day in _rebalances(calendar) if day > target), None)
    first_return = next((day for day in calendar if day > last_r), None)
    if (last_r not in _rebalances(calendar) or new_period['anchor_day'] != last_r
            or new_period['member_as_of'] != last_r or new_period['effective_from'] != first_return
            or new_period['next_rebalance_date'] != future_r or candidate['next_trade_date'] != future):
        raise ValueError('Recovery formal period clocks differ from original calendar')
    targets = [row for row in member_rows if row['rebalance_date'][:10] == last_r]
    if targets != new_period['target_members'] or len(targets) != 100 or len({row['symbol'] for row in targets}) != 100:
        raise ValueError('Recovery formal target member source differs')
    effective = sorted(_rows(exports[EXPORTS['effective']]), key=lambda row: int(row['rank']))
    names = {row['symbol'].zfill(6): row.get('name', '') for row in member_rows}
    actual_members = [dict(rank=int(row['rank']), symbol=row['symbol'].zfill(6), name=names.get(row['symbol'].zfill(6), ''), target_weight=.01) for row in effective]
    if (len(effective) != 100 or actual_members != new_period['effective_members']
            or any(row['as_of_date'][:10] > target for row in effective)):
        raise ValueError('Recovery executed members differ from formal effective source')
    events = [copy.deepcopy(row) for row in _rows(exports[EXPORTS['turnover']]) if row['rebalance_date'][:10] == last_r]
    for event in events:
        if (event.get('execution_timing') != 'close' or event.get('execution_date', '')[:10] != last_r
                or event.get('effective_date', '')[:10] != last_r):
            raise ValueError('Recovery formal event changes native close timing')
        if not event.get('return_start_date'):
            event.update(derived_return_start_date=first_return, return_start_date_source='independent_calendar_next_session_after_native_close')
        elif event['return_start_date'][:10] != first_return:
            raise ValueError('Recovery formal event first-return date differs')
    if not events or events != new_period['rebalance_events'] or new_period['package_id'] != sha(encode(dict(seed_date=last_r, members=actual_members, events=events))):
        raise ValueError('Recovery original period event/package identity differs')
    panel = {row['date'][:10]: row for row in _rows(exports[PANEL])}
    proxy = _rows(exports[EXPORTS['proxy']])
    if len(proxy) != len(new_rows) or any(row['date'][:10] != h['date'] or _number(row['close']) != _number(h['microcap'])
        or _number(panel[h['date']]['1.000852']) != _number(h['hedge']) for row, h in zip(proxy, new_rows)):
        raise ValueError('Recovery formal history is not its original index/panel')
    derived_state_raw = _unblob(proof['derived_state'])
    derived_states = json.loads(derived_state_raw)['versions']
    native_csv = proof['native_signal_csv']
    if set(native_csv) != set(VERSIONS):
        raise ValueError('Recovery native calculation CSV closure differs')
    from microcap_member_report_contract import _typed_csv_value
    import pandas as pd
    for version in VERSIONS:
        values = _rows(exports[EXPORTS[version]])
        if len(values) != 1 or values[0] != formal_states[version]:
            raise ValueError('Recovery formal version state differs from original CSV')
        for key in ('current_holding', 'next_holding', 'strategy_revision'):
            if str(derived_states[version].get(key)) != str(formal_states[version].get(key)):
                raise ValueError('Recovery native model state is not equivalent')
        calculated_rows = _rows(_unblob(native_csv[version]))
        if len(calculated_rows) != 1 or set(calculated_rows[0]) != set(derived_states[version]):
            raise ValueError('Recovery native calculated CSV/JSON full fields differ')
        typed = {key: _typed_csv_value(calculated_rows[0][key], value, key) for key, value in derived_states[version].items()}
        canonical = json.loads(pd.Series(typed).to_json(date_format='iso', force_ascii=False))
        if canonical != derived_states[version]:
            changed = [key for key in canonical if canonical[key] != derived_states[version].get(key)]
            raise ValueError('Recovery native calculated CSV/JSON full values differ: ' + version + ':' + ','.join(changed))
    if checkpoint['as_of_date'] == target and payload_bytes['version_state'] != derived_state_raw:
        raise ValueError('Recovery native derived state changed')
    return dict(target=target, parent=current_doc['as_of_date'], sessions=suffix, rebalance_sessions=rebalances)


def build(root, current_checkpoint, candidate_path, target, out, hedge_precision_witness=None):
    root = Path(root).resolve(); out = Path(out).resolve(); target = _day(target)
    _runtime(root)
    _latest(root, target)
    h, daily, _, _, precision = _native(root)
    current, old_payload = _read_package(Path(current_checkpoint), h)
    candidate, candidate_payload = _read_package(Path(candidate_path), h)
    if current.get('handoff_mode') == MODE:
        validate_recovery_payload(current, old_payload)
    old_period, old_rows, _ = _package(current, old_payload)
    formal_period, formal_rows, formal_states = _package(candidate, candidate_payload)
    precision.checked_bridge(old_period, old_payload['history'], current['as_of_date'], version_state_raw=old_payload['version_state'])
    manifest_raw, exports = _formal(root, target)
    if formal_period['source_manifest_sha256'] != sha(manifest_raw):
        raise ValueError('Recovery formal candidate does not bind current complete manifest')
    for name, expected in formal_period['source_files'].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or sha(path.read_bytes()) != expected:
            raise ValueError('Recovery formal candidate source changed: ' + name)
    calendar = _calendar(root, target)
    records = {item['date']: copy.deepcopy(item) for item in old_period.get(BRIDGE, {}).get('records', [])}
    if hedge_precision_witness is not None:
        witness = json.loads(Path(hedge_precision_witness).read_bytes())
        if (witness.get('schema') != 'member_hedge_precision_witness_v1'
                or witness.get('current_checkpoint_sha256') != sha(Path(current_checkpoint).read_bytes())
                or witness.get('candidate_checkpoint_sha256') != sha(Path(candidate_path).read_bytes())):
            raise ValueError('Recovery precision witness binds another pair')
        for item in witness['records']:
            day, _, _ = precision.checked_record(item)
            precision._formal_binding(item, formal_period)
            if day in records and records[day] != item:
                raise ValueError('Recovery precision witness revises an existing source')
            records[day] = item
    used = _compare_prefix(old_rows, formal_rows, records)
    history = _append(old_payload['history'], formal_rows[len(old_rows):])
    import pandas as pd
    frame = pd.read_csv(io.BytesIO(history)).set_index('date'); frame.index = pd.to_datetime(frame.index)
    turnover = pd.read_csv(io.BytesIO(candidate_payload['turnover']))
    old_frame = pd.read_csv(io.BytesIO(old_payload['history'])).set_index('date'); old_frame.index = pd.to_datetime(old_frame.index)
    old_turnover = pd.read_csv(io.BytesIO(old_payload['turnover']))
    old_states = json.loads(old_payload['version_state'])['versions']
    states = {}; native_csv = {}
    for version, module in daily.native_modules().items():
        previous, _ = daily.calculate_native(version, module, old_frame, old_turnover)
        for key in ('current_holding', 'next_holding', 'strategy_revision'):
            if str(previous.iloc[0].get(key)) != str(old_states[version].get(key)):
                raise ValueError('Recovery parent model state does not match its earned history: ' + version)
        computed, _ = daily.calculate_native(version, module, frame, turnover)
        row = computed.iloc[0].to_dict()
        state = daily.clean(row)
        for key in ('current_holding', 'next_holding', 'strategy_revision'):
            if str(state.get(key)) != str(formal_states[version].get(key)):
                raise ValueError('Recovery changes the formal native model state: ' + version)
        # Context flags belong to the independently passed full-D export. Math
        # fields come from the unchanged native calculation on earned history.
        for key in ('official_close_confirmed_signal', 'signal_timing'):
            if key in formal_states[version]:
                row[key] = formal_states[version][key]
        computed = pd.DataFrame([row])
        state = daily.clean(row)
        # Native JSON uses ISO midnight for Timestamp dates, while DataFrame
        # CSV defaults to YYYY-MM-DD. Publish the JSON's exact date spelling;
        # the session itself is independently fixed to D above.
        if str(row['date'])[:10] != str(state['date'])[:10] or str(state['date'])[:10] != target:
            raise ValueError('Recovery native calculation has another session')
        computed['date'] = state['date']
        native_csv[version] = _blob(computed.to_csv(index=False).encode('utf-8'))
        states[version] = state
    state_raw = encode(dict(schema='member_version_state_v1', versions=states))
    identity = dict(revision=CONTROLLER, source_sha256=sha(Path(__file__).read_bytes().replace(b'\r\n', b'\n')))
    proof = dict(schema=SCHEMA, controller=CONTROLLER, controller_identity=identity, target=target,
        calendar_sessions=calendar, sessions=[d for d in calendar if current['as_of_date'] < d <= target],
        rebalance_sessions=[d for d in _rebalances(calendar) if current['as_of_date'] < d <= target],
        parent=dict(checkpoint=_blob(Path(current_checkpoint).read_bytes()), **{k: _blob(v) for k, v in old_payload.items()}),
        formal=dict(checkpoint=_blob(Path(candidate_path).read_bytes()), manifest=_blob(manifest_raw),
                    exports={name: _blob(raw) for name, raw in exports.items()}, **{k: _blob(v) for k, v in candidate_payload.items()}),
        precision_records=[records[d] for d in sorted(used)], used_precision_dates=sorted(used),
        recovered_history_sha256=sha(history), recovered_history_bytes=len(history), derived_state=_blob(state_raw), native_signal_csv=native_csv,
        verified_at=datetime.now(timezone.utc).isoformat(), historical_rewrite=False, SMTP=False)
    if len(encode(proof)) > MAX_PROOF:
        raise ValueError('Recovery proof exceeds its explicit small-checkpoint budget')
    period = copy.deepcopy(formal_period)
    if BRIDGE in old_period:
        period[BRIDGE] = copy.deepcopy(old_period[BRIDGE])
    period[KEY] = proof
    payload = dict(candidate_payload, period=encode(period), history=history, version_state=state_raw)
    metadata = dict(as_of_date=target, next_trade_date=candidate['next_trade_date'], strategy_sha=candidate['strategy_sha'],
        period_status='current', handoff_mode=MODE, handoff_from=old_period['package_id'], recovery_controller=identity)
    names = dict(period='period.json', history='history.csv', turnover='turnover.csv', version_state='version_state.json', prices='member_prices.json')
    precheck = dict(schema='member_daily_checkpoint_v1', **metadata, **{k: dict(path=names[k], sha256=sha(v)) for k, v in payload.items()})
    validate_recovery_payload(precheck, payload)
    _latest(root, target)
    result = h._write_package(out, {k: (names[k], v) for k, v in payload.items()}, metadata)
    h.verify(out / 'checkpoint.json')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('bootstrap', 'recover'))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--current-checkpoint', type=Path)
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--hedge-precision-witness', type=Path)
    args = parser.parse_args(argv)
    if args.action == 'bootstrap':
        result = bootstrap_formal_candidate(args.root, args.target, args.out)
    else:
        if args.current_checkpoint is None or args.candidate is None:
            parser.error('recover needs --current-checkpoint and --candidate')
        result = build(args.root, args.current_checkpoint, args.candidate, args.target, args.out, args.hedge_precision_witness)
    print(json.dumps({k: v for k, v in result.items() if k != '_directory'}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
