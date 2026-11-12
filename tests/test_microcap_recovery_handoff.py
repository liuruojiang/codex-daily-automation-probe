"""Latest-D recovery software fixtures; never market/production acceptance."""
import copy
import csv
from datetime import date, datetime
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
from contextlib import contextmanager
import zipfile

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import microcap_recovery_handoff as m

NATIVE = Path(os.environ.get('MICROCAP_NATIVE_ROOT', os.environ.get('MICROCAP_RECOVERY_NATIVE_ROOT', r'D:\Codex\workspaces\microcap-source-reader-m7-20261112'))).resolve()


def csv_bytes(rows, fields=None):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=fields or list(rows[0]))
    writer.writeheader(); writer.writerows(rows)
    return stream.getvalue().encode('utf-8')


@contextmanager
def software_clock(target):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime.fromisoformat(target + 'T16:30:00+08:00')
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)
    with patch.object(m, 'datetime', Clock):
        yield


@pytest.fixture(scope='module')
def recovery_case(tmp_path_factory):
    if not NATIVE.is_dir():
        pytest.fail('This executable real-native fixture requires the reviewed M7 source checkout')
    temp = tmp_path_factory.mktemp('formal_gap')
    parent_dir = temp / 'parent'; parent_dir.mkdir()
    original = ROOT / 'tests/fixtures/member_checkpoint_local_read_20261111/checkpoint-13838054871.zip'
    with zipfile.ZipFile(original) as z:
        for name in z.namelist():
            target = parent_dir / name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(z.read(name))
    parent_path = parent_dir / 'checkpoint.json'
    actual_runtime = m._runtime(NATIVE)
    native = m._native(NATIVE)
    h, daily, calendar_module, _, _ = native
    parent = h.verify(parent_path)
    old = {key: h.read_bound(parent, key) for key in m.ROLES}
    old_history = m._history(old['history'])
    base = old_history[-1]
    added = [dict(base, date='2026-11-12'), dict(base, date='2026-11-13')]
    history = m._append(old['history'], added)
    data = temp / 'formal-software'; (data / 'outputs').mkdir(parents=True)
    # All suffix prices/clocks are explicitly controlled software observations.
    old_period = json.loads(old['period'])
    effective = [dict(rank=row['rank'], symbol=row['symbol'], as_of_date='2026-11-13') for row in old_period['effective_members']]
    members = [dict(rank=row['rank'], symbol=row['symbol'], name=row.get('name', ''), rebalance_date='2026-11-12') for row in old_period['effective_members']]
    event_rows = m._rows(old['turnover']); fields = list(event_rows[0])
    event = {key: '0' for key in fields}
    event.update(rebalance_date='2026-11-12', execution_timing='close', constraint_trade_date='2026-11-12',
                 execution_date='2026-11-12', effective_date='2026-11-12', return_start_date='2026-11-13', holding_count_after='100')
    turnover = csv_bytes(event_rows + [event], fields)
    rows = m._history(history)
    exports = {
        m.EXPORTS['proxy']: csv_bytes([dict(date=row['date'], close=row['microcap']) for row in rows]),
        m.EXPORTS['panel']: csv_bytes([{'date': row['date'], '1.000852': row['hedge']} for row in rows]),
        m.EXPORTS['effective']: csv_bytes(effective), m.EXPORTS['members']: csv_bytes(members), m.EXPORTS['turnover']: turnover,
    }
    frame = pd.read_csv(io.BytesIO(history)).set_index('date'); frame.index = pd.to_datetime(frame.index)
    table = pd.read_csv(io.BytesIO(turnover))
    for version, module in daily.native_modules().items():
        row, _ = daily.calculate_native(version, module, frame, table)
        row['official_close_confirmed_signal'] = True; row['signal_timing'] = 'close_confirmed'
        exports[m.EXPORTS[version]] = row.to_csv(index=False).encode('utf-8')
    inputs = {}
    for name, raw in exports.items():
        (data / name).write_bytes(raw); inputs[name] = m.sha(raw)
    for row in old_period['effective_members']:
        name = '.microcap_index_cache/prices_raw/' + row['symbol'] + '.csv'
        path = data / name; path.parent.mkdir(parents=True, exist_ok=True)
        raw = csv_bytes([dict(date='2026-11-13', close_raw='100')]); path.write_bytes(raw); inputs[name] = m.sha(raw)
    manifest = dict(ok=True, status='complete', scope='whole_workspace_delivery', expected_date='2026-11-13',
                    release_sha=m.NATIVE, inputs=inputs, artifacts={}, explicit_software_formal_envelope=True)
    (data / 'outputs/top100_delivery_manifest.json').write_bytes(m.encode(manifest))
    sessions_path = ROOT / 'tests/fixtures/exchange_sessions_20261015.json'
    if not sessions_path.exists():
        sessions_path = NATIVE / 'tests/fixtures/exchange_sessions_20261015.json'
    sessions = json.loads(sessions_path.read_bytes())['sessions']
    patches = [patch.object(m, '_native', lambda root: native), patch.object(m, '_runtime', lambda root: actual_runtime),
               patch.object(calendar_module, 'sessions_for_day', lambda _: tuple(date.fromisoformat(d) for d in sessions)),
               patch.object(calendar_module, 'latest_completed_session', lambda: date(2026, 11, 13))]
    for item in patches: item.start()
    try:
        candidate = temp / 'candidate'
        with software_clock('2026-11-13'):
            m.bootstrap_formal_candidate(data, '2026-11-13', candidate)
            recovered = temp / 'recovered'
            m.build(data, parent_path, candidate / 'checkpoint.json', '2026-11-13', recovered)
            cp = json.loads((recovered / 'checkpoint.json').read_bytes())
            payload = {key: (recovered / cp[key]['path']).read_bytes() for key in m.ROLES}
            yield dict(temp=temp, data=data, parent_path=parent_path, candidate=candidate / 'checkpoint.json',
                       checkpoint=cp, payload=payload, parent_raw=parent_path.read_bytes(), parent_payload=old, native=native)
    finally:
        for item in reversed(patches): item.stop()


def edit_payload(case, mutate):
    cp = copy.deepcopy(case['checkpoint']); payload = dict(case['payload'])
    period = json.loads(payload['period'])
    mutate(cp, payload, period)
    payload['period'] = m.encode(period)
    for key in m.ROLES:
        cp[key]['sha256'] = m.sha(payload[key])
    return cp, payload


def extend_formal_case(case, temp, target, new_rebalances, parent_path=None):
    """Native-calculated, explicitly software latest-D export from a real CP."""
    h, daily, _, _, _ = case['native']
    parent_path = Path(parent_path or case['parent_path'])
    parent = h.verify(parent_path)
    old = {key: h.read_bound(parent, key) for key in m.ROLES}
    period = json.loads(old['period'])
    calendar = m._calendar(NATIVE, target)
    last = m._history(old['history'])[-1]
    added = [dict(last, date=day) for day in calendar if parent['as_of_date'] < day <= target]
    history = m._append(old['history'], added)
    root = temp / 'formal'; (root / 'outputs').mkdir(parents=True)
    members = copy.deepcopy(period['target_members'])
    for rebalance in new_rebalances:
        members += [dict(rank=str(row['rank']), symbol=row['symbol'], name=row.get('name', ''), rebalance_date=rebalance) for row in period['effective_members']]
    event_rows = m._rows(old['turnover']); fields = list(event_rows[0])
    for rebalance in new_rebalances:
        event = {key: '0' for key in fields}
        event.update(rebalance_date=rebalance, execution_timing='close', constraint_trade_date=rebalance,
            execution_date=rebalance, effective_date=rebalance, return_start_date=next(day for day in calendar if day > rebalance), holding_count_after='100')
        event_rows.append(event)
    turnover = csv_bytes(event_rows, fields)
    rows = m._history(history)
    exports = {
        m.EXPORTS['proxy']: csv_bytes([dict(date=row['date'], close=row['microcap']) for row in rows]),
        m.EXPORTS['panel']: csv_bytes([{'date': row['date'], '1.000852': row['hedge']} for row in rows]),
        m.EXPORTS['effective']: csv_bytes([dict(rank=row['rank'], symbol=row['symbol'], as_of_date=target) for row in period['effective_members']]),
        m.EXPORTS['members']: csv_bytes(members), m.EXPORTS['turnover']: turnover,
    }
    frame = pd.read_csv(io.BytesIO(history)).set_index('date'); frame.index = pd.to_datetime(frame.index)
    table = pd.read_csv(io.BytesIO(turnover))
    for version, module in daily.native_modules().items():
        row, _ = daily.calculate_native(version, module, frame, table)
        row['official_close_confirmed_signal'] = True; row['signal_timing'] = 'close_confirmed'
        exports[m.EXPORTS[version]] = row.to_csv(index=False).encode()
    inputs = {}
    for name, raw in exports.items():
        (root / name).write_bytes(raw); inputs[name] = m.sha(raw)
    for member in period['effective_members']:
        name = '.microcap_index_cache/prices_raw/' + member['symbol'] + '.csv'
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True)
        raw = csv_bytes([dict(date=target, close_raw='100')]); path.write_bytes(raw); inputs[name] = m.sha(raw)
    (root / 'outputs/top100_delivery_manifest.json').write_bytes(m.encode(dict(ok=True, status='complete', scope='whole_workspace_delivery',
        expected_date=target, release_sha=m.NATIVE, inputs=inputs, artifacts={}, explicit_software_formal_envelope=True)))
    candidate = temp / 'candidate'; out = temp / 'recovered'
    with software_clock(target), patch.object(case['native'][2], 'latest_completed_session', lambda: date.fromisoformat(target)):
        m.bootstrap_formal_candidate(root, target, candidate)
        m.build(root, parent_path, candidate / 'checkpoint.json', target, out)
    cp = json.loads((out / 'checkpoint.json').read_bytes())
    payload = {key: (out / cp[key]['path']).read_bytes() for key in m.ROLES}
    return dict(checkpoint=cp, payload=payload, path=out / 'checkpoint.json')


def test_actual_native_latest_D_recovery_keeps_prefix_and_R_anchor(recovery_case):
    case = recovery_case
    result = m.validate_recovery_payload(case['checkpoint'], case['payload'])
    assert result['sessions'] == ['2026-11-12', '2026-11-13']
    assert result['rebalance_sessions'] == ['2026-11-12']
    assert json.loads(case['payload']['period'])['anchor_day'] == '2026-11-12'
    assert case['payload']['history'].startswith(case['parent_payload']['history'])
    assert case['parent_path'].read_bytes() == case['parent_raw']
    assert case['checkpoint']['handoff_mode'] == m.MODE
    assert case['checkpoint']['next_trade_date'] == '2026-11-16'
    case['native'][0].verify(case['temp'] / 'recovered/checkpoint.json')


@pytest.mark.parametrize('fault', ['mode', 'controller', 'parent_reference', 'skip_session', 'miss_R', 'false_next', 'member', 'derived_state', 'old_history', 'period_anchor'])
def test_recovery_transport_rejects_coherent_faults(recovery_case, fault):
    def mutate(cp, payload, period):
        proof = period[m.KEY]
        if fault == 'mode': cp['handoff_mode'] = 'native_close_extends_previous'
        elif fault == 'controller': cp['recovery_controller']['revision'] = 'unknown'
        elif fault == 'parent_reference':
            parent = json.loads(m._unblob(proof['parent']['checkpoint'])); parent['period_status'] = 'awaiting_handoff'
            proof['parent']['checkpoint'] = m._blob(m.encode(parent))
        elif fault == 'skip_session': proof['sessions'].remove('2026-11-12')
        elif fault == 'miss_R': proof['rebalance_sessions'] = []
        elif fault == 'false_next': cp['next_trade_date'] = '2099-01-01'
        elif fault == 'member': period['effective_members'][0]['symbol'] = '699999'
        elif fault == 'derived_state':
            state = json.loads(m._unblob(proof['derived_state'])); state['versions']['v2.0']['microcap_momentum'] = 99.0
            proof['derived_state'] = m._blob(m.encode(state)); payload['version_state'] = m.encode(state)
        elif fault == 'old_history': payload['history'] = payload['history'].replace(b'2010-', b'2011-', 1)
        else: period['anchor_day'] = '2026-11-13'
    cp, payload = edit_payload(recovery_case, mutate)
    with pytest.raises((ValueError, KeyError)):
        m.validate_recovery_payload(cp, payload)


def test_native_recovery_module_does_not_import_native_at_import_time():
    source = (ROOT / 'scripts/microcap_recovery_handoff.py').read_text(encoding='utf-8')
    import ast
    tree = ast.parse(source)
    imports = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not any('scripts.' in ast.unparse(node) or 'microcap_top100' in ast.unparse(node) for node in imports)


def test_proof_decompression_budget_rejects_a_bomb():
    import base64, zlib
    raw = b'x' * (m.MAX_BLOB + 1)
    with pytest.raises(ValueError):
        m._unblob(dict(sha256=m.sha(raw), encoding='zlib+base64', base64=base64.b64encode(zlib.compress(raw)).decode()))


def test_two_missed_Rs_recover_every_native_session_in_order(recovery_case, tmp_path):
    case = extend_formal_case(recovery_case, tmp_path, '2026-11-27', ['2026-11-12', '2026-11-26'])
    with software_clock('2026-11-27'):
        result = m.validate_recovery_payload(case['checkpoint'], case['payload'])
    assert result['rebalance_sessions'] == ['2026-11-12', '2026-11-26']
    assert result['sessions'] == [day for day in m._calendar(NATIVE, '2026-11-27') if '2026-11-11' < day <= '2026-11-27']
    assert json.loads(case['payload']['period'])['anchor_day'] == '2026-11-26'


def test_zero_R_daily_gap_keeps_original_period_and_prior_recovery(recovery_case, tmp_path):
    original_period = json.loads(recovery_case['payload']['period'])
    case = extend_formal_case(recovery_case, tmp_path, '2026-11-16', [], recovery_case['temp'] / 'recovered/checkpoint.json')
    with software_clock('2026-11-16'):
        result = m.validate_recovery_payload(case['checkpoint'], case['payload'])
    assert result['rebalance_sessions'] == [] and result['sessions'] == ['2026-11-16']
    period = json.loads(case['payload']['period'])
    for key in ('anchor_day', 'effective_from', 'effective_members', 'target_members', 'rebalance_events'):
        assert period[key] == original_period[key]
    assert case['payload']['history'].startswith(recovery_case['payload']['history'])


def test_actual_native_daily_descendant_keeps_recovery_proof(recovery_case, tmp_path):
    case = recovery_case
    h, daily, _, _, _ = case['native']
    period = json.loads(case['payload']['period']); basis = json.loads(case['payload']['prices'])
    quote_rows = [dict(symbol=row['symbol'], trade_date='2026-11-16', previous_date='2026-11-13',
        previous_raw_close=row['previous_raw_close'], raw_close=row['previous_raw_close'], previous_close_adj=row['previous_raw_close'],
        close_adj=row['previous_raw_close'], return_basis_revision='sina_ratio_ex_reference_v1', source='explicit software recovery descendant') for row in basis['rows']]
    quote = dict(market_date='2026-11-16', collected_at='2026-11-16T16:00:00+08:00', close_confirmed=True, rows=quote_rows)
    hedge = dict(market_date=quote['market_date'], collected_at=quote['collected_at'], close_confirmed=True,
        close=float(m._history(case['payload']['history'])[-1]['hedge']), source='explicit software recovery descendant')
    sessions = tuple(date.fromisoformat(day) for day in m._calendar(NATIVE, '2026-11-16'))
    result = daily.build(NATIVE, case['temp'] / 'recovered/checkpoint.json', quote, hedge, '2026-11-16', 'close_confirmed',
        tmp_path / 'daily', now=datetime.fromisoformat('2026-11-16T16:01:00+08:00'), calendar_sessions=sessions)
    assert result['status'] == 'OK'
    cp = h.verify(tmp_path / 'daily/checkpoint.json')
    payload = {key: h.read_bound(cp, key) for key in m.ROLES}
    assert json.loads(payload['period'])[m.KEY] == period[m.KEY]
    with software_clock('2026-11-16'):
        assert m.validate_recovery_payload(cp, payload)['target'] == '2026-11-13'
