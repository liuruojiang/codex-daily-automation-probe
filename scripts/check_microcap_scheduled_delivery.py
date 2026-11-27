"""Reconcile the previous session's 16:00 delivery and native handoff by readback."""
from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlencode
from zipfile import BadZipFile

from check_microcap_delivery import delivery_marker_name, verify_smtp_receipt, write_outputs
from dispatch_microcap_recovery import REPO, WORKFLOW as MAINTENANCE_WORKFLOW, ReadFailure, _runs, gh_api
from member_checkpoint_policy import validate_checkpoint_directory
from microcap_local_due import BJ, morning_target
from microcap_recovery_plan import SCHEMA as PLAN_SCHEMA
from prepare_microcap_local import encode, sha

WORKFLOW = 'microcap-member-daily.yml'
MODE = 'close_confirmed'


def _read_artifacts(api, repository, name):
    query = urlencode(dict(name=name, per_page=100))
    payload = api('GET', f'repos/{repository}/actions/artifacts?{query}')
    items = payload.get('artifacts')
    if (not isinstance(items, list) or type(payload.get('total_count')) is not int
            or payload['total_count'] != len(items)):
        raise ReadFailure('Incomplete artifact evidence for ' + name)
    for item in items:
        if (not isinstance(item, dict) or item.get('name') != name
                or type(item.get('expired')) is not bool or type(item.get('id')) is not int
                or type((item.get('workflow_run') or {}).get('id')) is not int):
            raise ReadFailure('Invalid artifact evidence for ' + name)
    return [item for item in items if not item['expired']]


def _slot(api, day, repository, *, clock=time(16), task='daily', workflow=WORKFLOW):
    planned = datetime.combine(day, clock, BJ).astimezone(timezone.utc)
    start = planned - timedelta(minutes=1)
    end = planned + timedelta(days=1)
    stamp = '%Y-%m-%dT%H:%M:%SZ'
    prefix = f'microcap-dispatch:microcap-{task}-' + planned.strftime('%Y%m%dT%H%M')
    rows = _runs(api, repository, workflow=workflow, branch='main', event='workflow_dispatch',
                 created=start.strftime(stamp) + '..' + end.strftime(stamp))
    matches = [
        dict(id=row['id'], status=row['status'], conclusion=row.get('conclusion'),
             created_at=row['created_at'], head_sha=row['head_sha'])
        for row in rows if re.fullmatch(re.escape(prefix) + r'[0-5][0-9][0-9]{3}Z', row.get('display_title', ''))
        and start <= datetime.fromisoformat(row['created_at'].replace('Z', '+00:00')) <= end
    ]
    return dict(planned_at=planned.astimezone(BJ).isoformat(), runs=matches,
                status='missing' if not matches else 'found' if len(matches) == 1 else 'multiple')


def _verify_live_receipt(repository, artifact, day):
    token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if not token:
        try:
            result = subprocess.run(['gh', 'auth', 'token'], capture_output=True, check=True, timeout=15)
            token = result.stdout.decode().strip()
        except (OSError, subprocess.SubprocessError) as exc:
            raise ReadFailure('Original SMTP receipt authentication unavailable') from exc
    if not token:
        raise ReadFailure('Original SMTP receipt authentication unavailable')
    return verify_smtp_receipt(repository, token, 'https://api.github.com', artifact, day, MODE,
                               require_archive_digest=True)


def _transaction(api, repository, name, day, verify_receipt):
    markers = _read_artifacts(api, repository, name)
    receipts = _read_artifacts(api, repository, name + '-smtp-accepted')
    result = dict(marker=name, marker_ids=[i['id'] for i in markers],
                  smtp_receipt_ids=[i['id'] for i in receipts])
    if receipts:
        proofs = [dict(verify_receipt(repository, item, day), artifact_id=item['id'],
                       run_id=item['workflow_run']['id']) for item in receipts]
        return dict(result, state='smtp_accepted' if len(receipts) == 1 else 'multiple_smtp_receipts',
                    smtp_run_ids=sorted({i['workflow_run']['id'] for i in receipts}), proofs=proofs)
    if markers:
        return dict(result, state='marker_without_verified_receipt')
    intents = _read_artifacts(api, repository, name + '-send-intent')
    return dict(result, state='send_outcome_unknown' if intents else 'no_send_evidence',
                intent_ids=[i['id'] for i in intents])


def _local_checkpoint(local, next_session, day):
    receipt = json.loads(Path(local).read_bytes())
    if 'checkpoint_receipt' in receipt:
        primary = receipt['checkpoint_receipt']
        raw = Path(primary['path']).read_bytes()
        if sha(raw) != primary['sha256'] or json.loads(raw) != primary['readback']:
            raise ValueError('Local preparation receipt and its machine source differ')
        receipt = json.loads(raw)
    if receipt.get('schema') == 'microcap_member_local_checkpoint_readback_v1':
        if (receipt.get('mode') != 'prepare' or receipt['date'] != next_session
                or receipt['as_of_date'] != day or receipt['native_verify_exit_code'] != 0):
            return None
        return Path(receipt['checkpoint']), receipt['checkpoint_sha256']
    if (receipt.get('schema') == 'ic_im_microcap_session_preparation_v1'
            and receipt.get('date') == next_session and receipt.get('status') == 'prepared'):
        return Path(receipt['checkpoint']['path']), receipt['checkpoint']['sha256']
    return None


def _handoff(day, plan_path, local_preparation, require_local):
    if plan_path is None:
        return dict(status='awaiting_native_plan', owner='existing_09_00_11_00_recovery')
    plan = json.loads(Path(plan_path).read_bytes())
    key = day.isoformat()
    if (plan.get('schema') != PLAN_SCHEMA or plan.get('target') != key
            or plan.get('latest_completed_session') != key or key not in plan['sessions']):
        raise ValueError('Native recovery plan is not bound to the audited completed session')
    checkpoint = Path(plan['latest_checkpoint_path'])
    raw = checkpoint.read_bytes()
    cp = json.loads(raw)
    validate_checkpoint_directory(checkpoint, None)
    period_raw = (checkpoint.parent / cp['period']['path']).read_bytes()
    period = json.loads(period_raw)
    if (sha(raw) != plan['latest_checkpoint_sha256'] or sha(period_raw) != cp['period']['sha256']
            or cp['period']['sha256'] != plan['latest_checkpoint_period_sha256']
            or period['anchor_day'] != plan['latest_checkpoint_period_anchor']
            or cp['as_of_date'] != plan['latest_checkpoint_as_of_date']
            or not period['anchor_day'] <= cp['as_of_date'] <= key
            or cp.get('period_status', 'current') != plan['latest_checkpoint_period_status']):
        raise ValueError('Original checkpoint differs from the native recovery plan')
    required = max(session for session in plan['rebalance_dates'] if session <= key)
    result = dict(rebalance_date=required, checkpoint_sha256=sha(raw),
                  checkpoint_session=cp['as_of_date'], period_sha256=cp['period']['sha256'],
                  period_anchor=period['anchor_day'], period_status=cp.get('period_status', 'current'))
    if period['anchor_day'] < required or result['period_status'] != 'current':
        return dict(result, status='rebalance_handoff_pending', owner='existing_09_00_11_00_recovery')
    if cp['as_of_date'] < key:
        return dict(result, status='daily_checkpoint_pending', owner='existing_09_00_11_00_recovery')
    if required != key:
        return dict(result, status='current_period_confirmed')
    next_session = min(session for session in plan['sessions'] if session > key)
    if period.get('effective_from') != next_session:
        raise ValueError('Current rebalance is not effective on the independently planned next session')
    result['next_consumer_session'] = next_session
    if local_preparation is not None and Path(local_preparation).is_file():
        local_cp = _local_checkpoint(local_preparation, next_session, key)
        if local_cp is not None:
            local_path, local_digest = local_cp
            local_raw = local_path.read_bytes()
            if (local_digest == sha(local_raw) == result['checkpoint_sha256']
                    and json.loads(local_raw).get('as_of_date') == key):
                validate_checkpoint_directory(local_path, None)
                return dict(result, status='verified_next_local_consumer',
                            local_preparation=str(local_preparation), local_checkpoint_sha256=local_digest)
        require_local = True
    if require_local:
        return dict(result, status='local_consumer_has_not_read_current_handoff',
                    owner='local_10_00_14_15_member_reader')
    return dict(result, status='current_handoff_ready_for_local_consumer',
                owner='local_10_00_14_15_member_reader')


def check(day, *, plan=None, local_preparation=None, require_local=False, api=gh_api, repository=REPO,
          verify_receipt=_verify_live_receipt):
    slot = _slot(api, day, repository)
    normal = _transaction(api, repository, delivery_marker_name(day, MODE), day, verify_receipt)
    accepted = {'smtp_accepted', 'multiple_smtp_receipts'}
    partial = None
    if normal['state'] in accepted:
        delivery = 'full_delivered'
        transaction = normal
    else:
        partial = _transaction(api, repository, normal['marker'] + '-partial-information', day, verify_receipt)
        delivery = 'partial_delivered' if partial['state'] in accepted else 'no_confirmed_delivery'
        transaction = partial
    slot['confirmed_delivery_from_slot'] = bool(
        {r['id'] for r in slot['runs']} & set(transaction.get('smtp_run_ids', [])))
    follow_up = []
    if slot['status'] != 'found':
        follow_up.append(dict(owner='microcap_16_00_trigger', action='diagnose_original_scheduled_slot',
                              status=slot['status']))
    if any(r['status'] != 'completed' for r in slot['runs']):
        follow_up.append(dict(owner='microcap_16_00_workflow', action='follow_original_run'))
    if any(x and x['state'] in ('send_outcome_unknown', 'marker_without_verified_receipt', 'multiple_smtp_receipts')
           for x in (normal, partial)):
        follow_up.append(dict(owner='microcap_16_00_delivery', action='reconcile_original_smtp_and_inbox'))
    if (transaction['state'] == 'smtp_accepted' and not transaction['marker_ids']
            and slot['status'] == 'found' and slot['confirmed_delivery_from_slot']
            and slot['runs'][0]['status'] == 'completed'):
        follow_up.append(dict(owner='microcap_16_00_delivery', action='recover_only_existing_delivery_marker',
            receipt_id=transaction['smtp_receipt_ids'][0], phase='normal' if transaction is normal else 'partial'))
    elif transaction['state'] in accepted and not transaction['marker_ids']:
        follow_up.append(dict(owner='microcap_16_00_delivery', action='reconcile_original_smtp_and_inbox'))
    if (delivery == 'no_confirmed_delivery'
            and not any(item['owner'] == 'microcap_16_00_delivery' for item in follow_up)
            and all(r['status'] == 'completed' for r in slot['runs'])):
        follow_up.append(dict(owner='microcap_16_00_delivery',
                              action='check_original_dated_report_before_manual_recovery'))
    try:
        handoff = _handoff(day, plan, local_preparation, require_local)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        handoff = dict(status='native_handoff_evidence_unconfirmed', reason=str(exc),
                       owner='existing_09_00_11_00_recovery')
        follow_up.append(dict(owner=handoff['owner'], action='verify_original_native_checkpoint'))
    if handoff.get('rebalance_date') == day.isoformat():
        rebalance = _slot(api, day, repository, clock=time(15, 10), task='rebalance', workflow=MAINTENANCE_WORKFLOW)
        handoff['original_rebalance_slot'] = rebalance
        if rebalance['status'] != 'found':
            follow_up.append(dict(owner='microcap_15_10_trigger', action='diagnose_original_scheduled_slot',
                                  status=rebalance['status']))
    else:
        rebalance = None
    pending = handoff['status'] in ('rebalance_handoff_pending', 'daily_checkpoint_pending')
    if pending and rebalance and any(row['status'] != 'completed' for row in rebalance['runs']):
        follow_up.append(dict(owner='microcap_15_10_workflow', action='follow_original_run'))
    elif pending:
        follow_up.append(dict(owner=handoff['owner'], action='resume_original_no_send_native_recovery'))
    if handoff['status'] == 'local_consumer_has_not_read_current_handoff':
        follow_up.append(dict(owner=handoff['owner'], action='verify_current_checkpoint_on_next_local_attempt'))
    return dict(schema='microcap_scheduled_delivery_reconciliation_v1', date=day.isoformat(), slot=slot,
                delivery=delivery, normal_transaction=normal, partial_transaction=partial,
                handoff=handoff, follow_up=follow_up)


def marker_recovery(day, phase, receipt_id, *, api=gh_api, repository=REPO, verify_receipt=_verify_live_receipt):
    if phase not in ('normal', 'partial') or type(receipt_id) is not int or receipt_id <= 0:
        raise ValueError('Exact delivery phase and original receipt ID are required')
    slot = _slot(api, day, repository)
    if slot['status'] != 'found' or slot['runs'][0]['status'] != 'completed':
        raise ValueError('The original 16:00 slot is missing, ambiguous or still active')
    name = delivery_marker_name(day, MODE) + ('-partial-information' if phase == 'partial' else '')
    transaction = _transaction(api, repository, name, day, verify_receipt)
    run_id = slot['runs'][0]['id']
    if (transaction['state'] != 'smtp_accepted' or transaction['smtp_receipt_ids'] != [receipt_id]
            or transaction['smtp_run_ids'] != [run_id]):
        raise ValueError('The original slot has no unique, exact verified SMTP acceptance receipt')
    return dict(schema='microcap_verified_delivery_marker_recovery_v1', date=day.isoformat(), phase=phase,
                original_scheduled_run_id=run_id, smtp_receipt=transaction['proofs'][0],
                marker=name, existing_marker_ids=transaction['marker_ids'],
                write_marker=not transaction['marker_ids'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--date')
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--local-preparation', type=Path)
    parser.add_argument('--require-local-consumer', action='store_true')
    parser.add_argument('--marker-only', choices=('normal', 'partial'))
    parser.add_argument('--receipt-id', type=int)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.marker_only:
        if not args.date or args.receipt_id is None or args.date != date.fromisoformat(args.date).isoformat():
            parser.error('Marker recovery requires the original dated receipt ID')
        result = marker_recovery(date.fromisoformat(args.date), args.marker_only, args.receipt_id)
    else:
        if args.root is None or args.receipt_id is not None:
            parser.error('Reconciliation requires an independently verified strategy calendar')
        sys.path.insert(0, str(args.root.resolve()))
        from scripts.exchange_calendar import latest_completed_session, sessions_for_day
        now = datetime.now(BJ)
        target = args.date or morning_target(now, sessions_for_day(now.date()))
        if target is None:
            result = dict(status='not_due', date=None)
        else:
            day = date.fromisoformat(target)
            if target != day.isoformat() or day not in sessions_for_day(day) or day > latest_completed_session():
                parser.error('An independently verified completed market session is required')
            try:
                result = check(day, plan=args.plan, local_preparation=args.local_preparation,
                               require_local=args.require_local_consumer)
            except (OSError, RuntimeError, ValueError, KeyError, TypeError, BadZipFile) as exc:
                result = dict(status='evidence_unconfirmed', date=target, reason=str(exc),
                              follow_up=[dict(owner='local_microcap_09_00_11_00',
                                              action='reconcile_original_evidence')])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('xb') as f:
        f.write(encode(result))
    print(json.dumps(result, ensure_ascii=False))
    if args.marker_only:
        write_outputs(dict(marker_name=result['marker'], write_marker=str(result['write_marker']).lower()))
    return int(bool(result.get('follow_up')))


if __name__ == '__main__':
    raise SystemExit(main())
