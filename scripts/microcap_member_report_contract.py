"""Read-only publication contracts for the fixed member-daily consumer.

No strategy calculation, calendar fetching or checkpoint rewriting belongs here.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
import io
import hashlib
import json
import math
from pathlib import Path
import re
from zoneinfo import ZoneInfo

BJ = ZoneInfo('Asia/Shanghai')
MEMBER_FLAGS = ('member_rebalance_required', 'member_rebalance_official', 'member_rebalance_actionable')
STRICT_NUMBERS = {'current_execution_scale', 'next_session_target_scale', 'next_session_actionable_scale'}


def _strict_day(value):
    if not isinstance(value, str) or re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value) is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def flag(value):
    if type(value) is bool:
        return value
    if isinstance(value,str):
        if value.strip().lower()=='true':return True
        if value.strip().lower()=='false':return False
    return None


def versions_for_date(result, expected_date, revisions):
    if (_strict_day(expected_date) is None or result.get('schema') != 'member_daily_result_v1'
            or result.get('market_date') != expected_date
            or result.get('publication_mode') not in {'intraday', 'close_confirmed'}):
        raise ValueError('Member daily result schema/date/mode mismatch')
    entries = result.get('versions')
    if not isinstance(entries, list) or len(entries) != len(revisions) or not all(isinstance(x, dict) for x in entries):
        raise ValueError('Member daily result needs three explicit version statuses')
    by_version = {entry.get('version'): entry for entry in entries}
    if set(by_version) != set(revisions) or len(by_version) != len(entries):
        raise ValueError('Member daily version identity mismatch')
    for version, entry in by_version.items():
        if entry.get('revision') != revisions[version] or entry.get('status') not in {'verified', 'partial', 'reference'}:
            raise ValueError('Member daily revision/status mismatch')
    return by_version


def validate_signal_identity(signal, version, revision, expected_date, mode, *, saved_close=False):
    if not isinstance(signal,dict):raise ValueError('Final signal is missing: '+version)
    row_day=_event_day(signal.get('date')) if saved_close else signal.get('date')
    if (signal.get('strategy_revision') != revision
            or row_day != expected_date
            or (signal.get('quote_trade_date') != expected_date and not (saved_close and 'quote_trade_date' not in signal))
            or (signal.get('quote_coverage') != '100/100' and not (saved_close and 'quote_coverage' not in signal))):
        raise ValueError('Final signal revision/date/member coverage differs: ' + version)
    if signal.get('version', version[1:]) != version[1:] or signal.get('strategy_version', version) != version:
        raise ValueError('Final signal version identity differs: ' + version)
    confirmed = flag(signal.get('official_close_confirmed_signal'))
    timing = signal.get('signal_timing')
    if mode == 'close_confirmed':
        if confirmed is not True or timing not in {'close_confirmed', 'close_confirmed_anchor'}:
            raise ValueError('Final signal timing/official flag is not confirmed close: ' + version)
    elif confirmed is not False or timing != 'intraday_hypothetical_if_now_close':
        raise ValueError('Final signal timing/official flag is not intraday: ' + version)


def validate_publication_clock(result, expected_date, now):
    try:
        generated = datetime.fromisoformat(result['generated_at'])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('Member result requires its original generation clock') from exc
    if generated.tzinfo is None or now.tzinfo is None or generated > now:
        raise ValueError('Member result generation clock is naive or future')
    local = generated.astimezone(BJ)
    if result['publication_mode'] == 'close_confirmed':
        close = datetime.fromisoformat(expected_date + 'T15:00:00+08:00')
        if generated < close or now < close:
            raise ValueError('Confirmed-close publication precedes its market close')
    elif (local.date().isoformat() != expected_date or local.time().replace(tzinfo=None) >= time(15)
          or now.astimezone(BJ).date().isoformat() != expected_date
          or now.astimezone(BJ).time().replace(tzinfo=None) >= time(15)):
        raise ValueError('Intraday generation and publication must remain on-session and before 15:00')


def _execution_identity(signal,version):
    active='long_microcap_top100' if version=='v2.5' else 'long_microcap_short_zz1000'
    for holding_key,scale_key in (('current_holding','current_execution_scale'),('next_holding','next_session_target_scale')):
        holding=signal.get(holding_key)
        if holding not in {'cash',active}:
            raise ValueError('Final signal has an unsupported holding: '+version+'.'+holding_key)
        try:
            if type(signal.get(scale_key)) is bool:raise ValueError('boolean execution scale')
            scale=float(signal[scale_key])
        except (KeyError,TypeError,ValueError) as exc:
            raise ValueError('Final signal lacks its execution scale: '+version+'.'+scale_key) from exc
        if not math.isfinite(scale) or scale!=(0 if holding=='cash' else 1):
            raise ValueError('Final signal holding/scale conflict: '+version+'.'+scale_key)


def _typed_csv_value(raw, expected, field):
    if expected is None:
        if raw != '':
            raise ValueError('Final CSV and result conflict: ' + field)
        return None
    if type(expected) is bool:
        value = flag(raw)
        if value is None:
            raise ValueError('Final CSV has an invalid boolean: ' + field)
        return value
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError('Final CSV has an invalid number: ' + field) from exc
        if not math.isfinite(value) or not math.isfinite(expected):
            raise ValueError('Final CSV/result has a nonfinite number: ' + field)
        if field in STRICT_NUMBERS and value != expected:
            raise ValueError('Final CSV and result conflict: ' + field)
        return value
    if not isinstance(expected, str) or not isinstance(raw, str):
        raise ValueError('Final CSV/result has an unsupported scalar: ' + field)
    return raw


def checkpoint_proof(path):
    """Read one exact source checkpoint's five-file closure, without adopting it."""
    path=Path(path);raw=path.read_bytes();cp=json.loads(raw)
    if cp.get('schema')!='member_daily_checkpoint_v1':raise ValueError('Not a saved member checkpoint')
    proof=dict(checkpoint=cp,sha256=hashlib.sha256(raw).hexdigest(),files={'checkpoint.json':raw})
    for key in ('period','history','turnover','version_state','prices'):
        spec=cp[key];source=(path.parent/spec['path']).resolve()
        if Path(spec['path']).is_absolute() or not source.is_relative_to(path.parent.resolve()):raise ValueError('Saved checkpoint path escape')
        data=source.read_bytes()
        if hashlib.sha256(data).hexdigest()!=spec['sha256']:raise ValueError('Saved checkpoint hash differs: '+key)
        proof['files'][spec['path']]=data
        proof[key]=(list(csv.DictReader(io.StringIO(data.decode('utf-8-sig'))))
                    if key in ('history','turnover') else json.loads(data))
    if len(proof['files'])!=6:raise ValueError('Saved checkpoint dependency paths overlap')
    return proof


def saved_close_identity(result,context,expected_date,revisions):
    """Recognize 477's original saved-close protocol, never fabricate quote fields."""
    if result.get('reused_close_checkpoint') is not True:return False
    if (result.get('collection_performed') is not False or result.get('publication_mode')!='close_confirmed'
            or result.get('quote_coverage_basis')!='saved_checkpoint_member_prices'
            or result.get('quote_coverage')!='100/100' or result.get('member_count')!=100
            or result.get('anchor_date')!=expected_date):
        raise ValueError('Saved-close protocol flags/date/coverage differ')
    proof=(context or {}).get('saved_close_proof')
    if not isinstance(proof,dict):raise ValueError('Saved-close source checkpoint proof is missing')
    try:
        cp,period,prices,state=(proof[key] for key in ('checkpoint','period','prices','version_state'))
        files=proof['files'];raw_cp=files['checkpoint.json']
        if hashlib.sha256(raw_cp).hexdigest()!=proof['sha256'] or json.loads(raw_cp)!=cp:
            raise ValueError('Saved-close raw checkpoint proof differs')
        for key in ('period','history','turnover','version_state','prices'):
            value=files[cp[key]['path']]
            if hashlib.sha256(value).hexdigest()!=cp[key]['sha256']:
                raise ValueError('Saved-close dependency proof differs: '+key)
            decoded=(list(csv.DictReader(io.StringIO(value.decode('utf-8-sig'))))
                     if key in ('history','turnover') else json.loads(value))
            if decoded!=proof[key]:raise ValueError('Saved-close decoded proof differs: '+key)
        if (proof['sha256']!=result.get('checkpoint_sha256') or cp['as_of_date']!=expected_date
                or cp['period']['sha256']!=result.get('period_sha256')
                or _strict_day(cp.get('next_trade_date')) is None or cp['next_trade_date']<=expected_date
                or prices.get('schema')!='member_price_basis_v1' or prices['as_of_date']!=expected_date
                or state.get('schema')!='member_version_state_v1' or set(state['versions'])!=set(revisions)):
            raise ValueError('Saved-close source identity/date differs')
        phase=cp.get('period_status','current')
        reference=phase=='awaiting_handoff'
        if (phase not in {'current','awaiting_handoff'}
                or result.get('period_status')!=('overdue_reference' if reference else 'current')
                or result.get('status')!=('PARTIAL' if reference else 'OK')
                or any(item.get('status')!=('reference' if reference else 'verified') for item in result['versions'])
                or re.fullmatch('[0-9a-f]{64}',str(period.get('source_manifest_sha256',''))) is None
                or not isinstance(period.get('source_files'),dict) or not period['source_files']):
            raise ValueError('Saved-close phase or original source closure differs')
        symbols=[row['symbol'] for row in period['effective_members']]
        if (period.get('schema')!='member_period_v1' or len(symbols)!=100 or len(set(symbols))!=100
                or any(not isinstance(s,str) or re.fullmatch('[0-9]{6}',s) is None for s in symbols)
                or [int(row['rank']) for row in period['effective_members']]!=list(range(1,101))
                or any(abs(float(row.get('target_weight',.01))-.01)>1e-12 for row in period['effective_members'])
                or sorted(row['symbol'] for row in prices['rows'])!=sorted(symbols)):
            raise ValueError('Saved-close member price closure differs')
        for row in prices['rows']:
            value=float(row['previous_raw_close'])
            if (type(row['previous_raw_close']) is bool or row['previous_raw_date']!=expected_date
                    or not math.isfinite(value) or value<=0):
                raise ValueError('Saved-close member price date/value differs')
        history=proof['history'];days=[_event_day(row['date']) for row in history]
        if not days or None in days or days!=sorted(set(days)) or days[-1]!=expected_date:
            raise ValueError('Saved-close earned history dates differ')
        if any(not math.isfinite(float(row[key])) or float(row[key])<=0 for row in history for key in ('microcap','hedge')):
            raise ValueError('Saved-close earned history value differs')
        for item in result['versions']:
            signal=item['signal'];original=state['versions'][item['version']]
            if (signal!=original or original.get('strategy_revision')!=revisions[item['version']]
                    or _event_day(original.get('date'))!=expected_date):
                raise ValueError('Saved-close signal and source version state differ')
    except (KeyError,TypeError,AttributeError) as exc:
        raise ValueError('Saved-close checkpoint proof is incomplete') from exc
    return True


def publication_context(result,context,expected_date,revisions):
    """A certified original saved checkpoint owns its display/action context."""
    context=context or {}
    if not saved_close_identity(result,context,expected_date,revisions):return context
    proof=context['saved_close_proof']
    return dict(context,checkpoint=proof['checkpoint'],period=proof['period'],history=proof['history'],
        turnover=proof['turnover'],member_contract_bound=True)


def final_signal_files(result, root, expected_date, revisions, *, context=None):
    """Require every published signal's full CSV row to match its native JSON.

    Native 477 writes the full-precision CSV first, then serializes its JSON
    with Series.to_json's default double_precision=10. Apply that exact native
    serialization to typed CSV values; do not introduce an isclose tolerance.
    Keep those validated original bytes for the attachment and receipt binding.
    """
    entries = versions_for_date(result, expected_date, revisions)
    saved=saved_close_identity(result,context,expected_date,revisions)
    status = 'OK' if all(x['status'] == 'verified' for x in entries.values()) else 'PARTIAL'
    if result.get('status') != status:
        raise ValueError('Member result status conflicts with its three version statuses')
    if result.get('period_status') == 'overdue_reference' and any(x['status'] == 'verified' for x in entries.values()):
        raise ValueError('Old-period observations cannot be verified current targets')
    files = {}
    for version, entry in entries.items():
        name = version.replace('.', '_') + '_signal.csv'
        path = Path(root) / name
        signal = entry.get('signal')
        if signal is None and entry['status'] == 'partial':
            if path.exists():
                raise ValueError('Partial version has an unbound final CSV: ' + version)
            continue
        validate_signal_identity(signal, version, revisions[version], expected_date, result['publication_mode'],saved_close=saved)
        _execution_identity(signal,version)
        try:
            raw = path.read_bytes()
            reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig'), newline=''), strict=True)
            rows = list(reader)
        except (OSError, UnicodeError, csv.Error) as exc:
            raise ValueError('Missing or corrupt final CSV: ' + version) from exc
        header = reader.fieldnames or []
        if (len(rows) != 1 or not header or len(header) != len(set(header))
                or set(header) != set(signal) or None in rows[0]
                or any(value is None for value in rows[0].values())):
            raise ValueError('Final CSV field/row closure differs from result: ' + version)
        typed = {key: _typed_csv_value(rows[0][key], expected, key) for key, expected in signal.items()}
        # Lazy import: checkpoint discovery imports REVISIONS before the daily
        # pandas runtime is installed, but it does not call this publication gate.
        import pandas as pd
        canonical = json.loads(pd.Series(typed).to_json(date_format='iso', force_ascii=False))
        for field, expected in signal.items():
            if canonical[field] != expected or (type(expected) is bool and type(canonical[field]) is not bool):
                raise ValueError('Final CSV and result conflict: ' + version + '.' + field)
        files[version] = raw
    return files


def _event_day(value):
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    if stamp.tzinfo is not None or stamp.time() != time():
        return None
    return stamp.date().isoformat()


def _count(value):
    if isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (ValueError, InvalidOperation):
        return None
    if not number.is_finite() or number != number.to_integral_value() or not 0 <= number <= 100:
        return None
    return int(number)


def _event_identity(event, signal_day, next_day):
    returned = event.get('return_start_date') or event.get('derived_return_start_date')
    if (event.get('execution_timing') != 'close' or _event_day(event.get('rebalance_date')) != signal_day
            or _event_day(event.get('execution_date')) != signal_day
            or _event_day(event.get('effective_date')) != signal_day or _event_day(returned) != next_day):
        return None
    if not event.get('return_start_date') and event.get('return_start_date_source') != 'independent_calendar_next_session_after_native_close':
        return None
    counts = tuple(_count(event.get(key)) for key in ('entry_count', 'exit_count'))
    return counts if None not in counts else None


def _bound_period_contract(result, context, next_day):
    cp, period = context.get('checkpoint', {}), context.get('period', {})
    day = result['market_date']
    if (not context.get('member_contract_bound') or cp.get('schema') != 'member_daily_checkpoint_v1'
            or cp.get('period_status', 'current') != 'current' or result.get('period_status') != 'current'
            or period.get('schema') != 'member_period_v1' or period.get('anchor_day') != day
            or period.get('effective_from') != next_day or result.get('period_sha256') != cp.get('period', {}).get('sha256')
            or re.fullmatch('[0-9a-f]{64}', str(period.get('source_manifest_sha256', ''))) is None
            or not isinstance(period.get('source_files'), dict) or not period['source_files']):
        return None
    events = period.get('rebalance_events', [])
    turnover = [row for row in context.get('turnover', []) if _event_day(row.get('rebalance_date')) == day]
    if not events or len(events) != len(turnover):
        return None
    planned = [_event_identity(row, day, next_day) for row in events]
    earned = [_event_identity(row, day, next_day) for row in turnover]
    # A final-close turnover row may omit its next-return date; the period
    # contains the explicitly derived independent-calendar link from handoff.
    for index, row in enumerate(turnover):
        if earned[index] is None and not row.get('return_start_date'):
            candidate = dict(row, derived_return_start_date=next_day,
                return_start_date_source='independent_calendar_next_session_after_native_close')
            earned[index] = _event_identity(candidate, day, next_day)
    if None in planned or None in earned or sorted(planned) != sorted(earned):
        return None
    entered, exited = sum(x[0] for x in planned), sum(x[1] for x in planned)
    if not 0 < entered + exited or max(entered, exited) > 100:
        return None
    return dict(member_rebalance_required=True, member_rebalance_official=True, member_rebalance_actionable=True,
        member_rebalance_signal_date=day, member_rebalance_execution_date=next_day,
        member_enter_count=entered, member_exit_count=exited, source='bound_period_turnover')


def member_action(result, version, signal, context):
    """Return an action only for an official current-close, next-session rotation."""
    cp = context.get('checkpoint', {})
    day, next_day = result['market_date'], cp.get('next_trade_date')
    active = 'long_microcap_top100' if version == 'v2.5' else 'long_microcap_short_zz1000'
    if (result['publication_mode'] != 'close_confirmed'
            or signal.get('current_holding') != active or signal.get('next_holding') != active):
        return None
    try:
        scales = [float(signal[key]) for key in ('current_execution_scale', 'next_session_target_scale')]
    except (KeyError, TypeError, ValueError):
        return None
    if any(not math.isfinite(value) or value <= 0 for value in scales):
        return None
    explicit = any(key in signal for key in MEMBER_FLAGS)
    if explicit:
        flags=[flag(signal.get(key)) for key in MEMBER_FLAGS]
        # A stated preview/non-official or historical event remains background.
        if False in flags or signal.get('member_rebalance_signal_date')!=day:return None
        if None in flags or _strict_day(signal.get('member_rebalance_execution_date')) is None:
            raise ValueError('Current member rotation has an incomplete dated contract')
        claimed=True
    else:
        period=context.get('period',{})
        claimed=(period.get('anchor_day')==day and result.get('period_status')=='current'
            and any((_count(row.get('entry_count')) or 0)+(_count(row.get('exit_count')) or 0)>0
                    for row in period.get('rebalance_events',[])))
    if cp.get('as_of_date')!=day or _strict_day(next_day) is None or next_day<=day:
        if claimed:raise ValueError('Current member rotation next execution session cannot be certified')
        return None
    bound = _bound_period_contract(result, context, next_day)
    if bound is None:
        if claimed:raise ValueError('Current member rotation cannot be certified from bound period/turnover')
        return None
    contract = dict(signal, source='final_csv') if explicit else bound
    if (not contract or any(flag(contract.get(key)) is not True for key in MEMBER_FLAGS)
            or contract.get('member_rebalance_signal_date') != day
            or contract.get('member_rebalance_execution_date') != next_day):
        if claimed:raise ValueError('Current member rotation conflicts with its next-session contract')
        return None
    entered, exited = (_count(contract.get(key)) for key in ('member_enter_count', 'member_exit_count'))
    if entered is None or exited is None or entered + exited == 0:
        if claimed:raise ValueError('Current member rotation counts cannot be certified')
        return None
    if explicit:
        if bound['member_enter_count'] != entered or bound['member_exit_count'] != exited:
            raise ValueError('Current member rotation counts conflict with bound turnover')
    return dict(signal_date=day, execution_date=next_day, entry_count=entered, exit_count=exited,
        official=True, actionable=True, required=True, source=contract['source'],
        text=f'名单调仓（调入 {entered}，调出 {exited}；信号日 {day}，执行日 {next_day}）')
