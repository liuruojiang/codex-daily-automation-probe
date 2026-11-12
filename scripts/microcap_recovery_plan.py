"""Plan missed native periods against the real completed close, without sending.

Signal dates are never replaced by the date on which a recovery happens.  The
small checkpoint is a planning observation; a later awaiting-handoff package is
never selected as the formal parent of a missed period.
"""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from prepare_microcap_local import atomic, encode


SCHEMA = 'microcap_scoped_recovery_plan_v1'
OBSERVE = r'''
import hashlib,json,subprocess,sys
from datetime import date
from pathlib import Path
root=Path(sys.argv[1]).resolve();checkpoint=Path(sys.argv[2]).resolve()
sys.path.insert(0,str(root))
from scripts.member_period_handoff import verify,read_bound
from scripts.exchange_calendar import latest_completed_session,sessions_for_day
from microcap_top100_mom16_biweekly_live_v2_0 import base_mod
import pandas as pd
actual=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],encoding='utf-8').strip()
if actual!=sys.argv[4]:raise ValueError('Recovery planner consumer differs from exact requested runtime')
completed=latest_completed_session().isoformat()
if completed!=sys.argv[3]:raise ValueError('Recovery planner date is not the latest completed exchange session')
cp=verify(checkpoint);period=json.loads(read_bound(cp,'period'))
sessions=pd.DatetimeIndex(sessions_for_day(date.fromisoformat(completed))).normalize()
rebalances=base_mod.build_biweekly_rebalance_dates(sessions)
result=dict(consumer_sha=actual,latest_completed_session=completed,
 sessions=[str(x.date()) for x in sessions],rebalance_dates=[str(x.date()) for x in rebalances],
 checkpoint_path=str(checkpoint),checkpoint_sha256=hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
 checkpoint_as_of_date=cp['as_of_date'],checkpoint_period_status=cp.get('period_status','current'),
 checkpoint_period_anchor=period['anchor_day'],checkpoint_period_sha256=cp['period']['sha256'],
 checkpoint_members=len(period['effective_members']),native_checkpoint_verified=True)
print(json.dumps(result))
'''


def canonical_day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError('Recovery plan requires canonical exchange dates')
    return value


def ordered_days(values, kind):
    if not isinstance(values, list) or not values:
        raise ValueError('Recovery plan requires an independent ' + kind)
    checked = [canonical_day(item) for item in values]
    if checked != sorted(set(checked)):
        raise ValueError('Recovery calendar is duplicated or unordered: ' + kind)
    return checked


def build_plan(observation, completed, consumer_sha, *, task='rebalance_recovery'):
    """Pure decision logic; the CLI obtains observation from real native code."""
    completed = canonical_day(completed)
    if task not in ('rebalance', 'rebalance_recovery'):
        raise ValueError('Recovery planning task differs from its no-send entrypoints')
    if (not isinstance(observation, dict)
            or re.fullmatch('[0-9a-f]{40}', consumer_sha or '') is None
            or observation.get('consumer_sha') != consumer_sha
            or observation.get('native_checkpoint_verified') is not True
            or observation.get('latest_completed_session') != completed):
        raise ValueError('Recovery observation has another runtime, date or unverified checkpoint')
    sessions = ordered_days(observation.get('sessions'), 'session calendar')
    rebalances = ordered_days(observation.get('rebalance_dates'), 'rebalance calendar')
    if not set(rebalances) <= set(sessions) or completed not in sessions:
        raise ValueError('Recovery rebalance/session calendar differs')
    anchor = canonical_day(observation.get('checkpoint_period_anchor'))
    previous = canonical_day(observation.get('checkpoint_as_of_date'))
    state = observation.get('checkpoint_period_status')
    if (state not in ('current', 'awaiting_handoff') or anchor not in rebalances
            or previous not in sessions or anchor > previous or previous > completed
            or type(observation.get('checkpoint_members')) is not int
            or observation['checkpoint_members'] != 100):
        raise ValueError('Recovery checkpoint has an invalid period, members or future close')
    for field in ('checkpoint_sha256', 'checkpoint_period_sha256'):
        if re.fullmatch('[0-9a-f]{64}', observation.get(field, '')) is None:
            raise ValueError('Recovery checkpoint identity is not hash bound')
    if not isinstance(observation.get('checkpoint_path'), str) or not observation['checkpoint_path']:
        raise ValueError('Recovery checkpoint path is missing')
    required = [item for item in rebalances if anchor < item <= completed]
    if state == 'awaiting_handoff' and not required:
        raise ValueError('Awaiting-handoff checkpoint has no missing native period')
    advance = previous < completed
    run = bool(required or (advance and task == 'rebalance_recovery'))
    first = required[0] if required else None
    last = required[-1] if required else anchor
    parent_ceiling = sessions[sessions.index(first) - 1] if first and sessions.index(first) else previous if not first else None
    if first and parent_ceiling is None:
        raise ValueError('Independent calendar does not cover the missed period parent')
    next_session = None
    if first:
        following = sessions[sessions.index(first) + 1:]
        if not following:
            raise ValueError('Independent calendar does not cover the missed period return start')
        next_session = following[0]
    return dict(
        schema=SCHEMA, status='recovery_due' if run else 'no_recovery_due', run=run,
        action='rebalance_forward' if required else 'daily_catchup' if advance and run else 'noop',
        scheduled_task=task,
        target=completed, delivery_date=completed, consumer_sha=consumer_sha,
        recovery_R=first, required_rebalance_dates=required,
        last_required_R=last, parent_not_after_date=parent_ceiling,
        recovery_next_session=next_session, needs_checkpoint_advance=advance,
        latest_checkpoint_path=observation['checkpoint_path'],
        latest_checkpoint_sha256=observation['checkpoint_sha256'],
        latest_checkpoint_as_of_date=previous, latest_checkpoint_period_status=state,
        latest_checkpoint_period_anchor=anchor,
        latest_checkpoint_period_sha256=observation['checkpoint_period_sha256'],
        latest_completed_session=completed, calendar_source='independent_native_exchange_calendar',
        sessions=sessions, rebalance_dates=rebalances,
        source_collection_complete_does_not_skip_recovery=True,
        formal_parent_is_latest_checkpoint=not required and state == 'current',
        native_formal_gates_required=run, email_sent=False, orders_placed=False,
    )


def observe(consumer, checkpoint, completed, consumer_sha):
    environment = os.environ.copy()
    environment['PYTHONDONTWRITEBYTECODE'] = '1'
    environment['PYTHONUTF8'] = '1'
    result = subprocess.run(
        [sys.executable, '-B', '-X', 'utf8', '-c', OBSERVE,
         str(Path(consumer).resolve()), str(Path(checkpoint).resolve()), completed, consumer_sha],
        capture_output=True, encoding='utf-8', env=environment,
    )
    if result.returncode:
        raise RuntimeError('Native recovery observation failed: ' + result.stderr.strip())
    return json.loads(result.stdout)


def write_plan(path, plan):
    path = Path(path)
    raw = encode(plan)
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError('Recovery plan destination already contains another immutable plan')
    else:
        atomic(path, raw)
    return hashlib.sha256(raw).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--consumer', type=Path, required=True)
    parser.add_argument('--consumer-sha', required=True)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--date', required=True)
    parser.add_argument('--task', choices=('rebalance', 'rebalance_recovery'), required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--github-output', type=Path)
    args = parser.parse_args()
    observation = observe(args.consumer, args.checkpoint, args.date, args.consumer_sha)
    plan = build_plan(observation, args.date, args.consumer_sha, task=args.task)
    digest = write_plan(args.out, plan)
    if args.github_output:
        with args.github_output.open('a', encoding='utf-8') as output:
            for key, value in (('run', str(plan['run']).lower()), ('date', plan['delivery_date']),
                               ('recovery_R', plan['recovery_R'] or ''),
                               ('parent_not_after_date', plan['parent_not_after_date'] or ''),
                               ('action', plan['action']), ('sha256', digest)):
                output.write(key + '=' + value + '\n')
    print(json.dumps(dict(plan, plan_sha256=digest), ensure_ascii=False))


if __name__ == '__main__':
    main()
