"""Plan a local morning run/catch-up using exchange sessions and real receipts."""
from __future__ import annotations

import argparse
from datetime import datetime, time
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

from maintain_microcap_local import hashes, bound, CACHE, observation_floor

BJ = ZoneInfo('Asia/Shanghai')


def morning_target(now, sessions):
    if now.tzinfo is None:
        raise ValueError('Catch-up clock must include a timezone')
    current = now.astimezone(BJ)
    if current.date() not in sessions or current.time().replace(tzinfo=None) < time(9):
        return None
    # Even an evening manual recovery belongs to this morning's target.
    # It cannot silently become a new same-day close-confirmed run.
    previous = [d for d in sessions if d < current.date()]
    if not previous:
        raise ValueError('Independent calendar has no prior completed session')
    return max(previous).isoformat()


def completed(directory, root, target, files_out=None, required_asof=None):
    directory = Path(directory)
    path = directory / 'result.json'
    if not path.exists(): return False
    result = json.loads(path.read_bytes())
    if (result.get('status') != 'complete' or result.get('target') != target
            or result.get('total') != 300 or result.get('complete') != 300
            or result.get('failures') or result.get('notice_status') != 'complete'
            or result.get('metadata_status') != 'complete' or result.get('current_names_status')!='complete'):
        return False
    records = [directory / (symbol + '-' + stage + '.json')
               for symbol in result.get('symbols', []) for stage in ('price', 'share', 'ratio')]
    if len(records) != 900 or len(set(result['symbols'])) != 300:
        return False
    records += [directory / 'notice.json', directory / 'metadata.json',directory/'current-names.json']
    context = result['context']
    all_files = {}
    for record_path in records:
        record = json.loads(record_path.read_bytes())
        if record.get('status') != 'complete' or record.get('context') != context:
            return False
        for name, digest in record['files'].items():
            if name in all_files and all_files[name] != digest: return False
            all_files[name] = digest
    if hashes(root, all_files) != all_files: return False
    if required_asof is not None:
        # An evening rehearsal for tomorrow's price target is not tomorrow's
        # morning ST/name observation. Prices can be reused; these sources cannot.
        proof=json.loads(bound(root,CACHE+'st_notice_sources/freshness.json').read_bytes())
        names=json.loads(bound(root,CACHE+'local_current_names/'+target+'.json').read_bytes())
        for stamp in (proof['required_asof'],names['observed_at']):
            observed=datetime.fromisoformat(stamp)
            if observed.tzinfo is None or not required_asof<=observed<=datetime.now(BJ): return False
    if files_out is not None: files_out.update(all_files)
    return True


def plan(now, sessions, state, root):
    target = morning_target(now, sessions)
    if target is None:
        return dict(status='not_due', target=None, action='no_collection')
    try:
        done = completed(Path(state) / 'runs' / target, root, target, required_asof=observation_floor(now))
    except (OSError, ValueError, KeyError, TypeError):
        return dict(status='evidence_needs_revalidation', target=target,
                    action='inspect_and_resume_affected_native_stages')
    return dict(status='already_complete' if done else 'due', target=target,
                action='no_collection' if done else 'resume_native_maintenance',
                automatic_on_wakeup=False)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--state-root', type=Path, required=True)
    args = p.parse_args()
    sys.path.insert(0, str(args.root.resolve()))
    from scripts.exchange_calendar import sessions_for_day
    now = datetime.now(BJ)
    result = plan(now, sessions_for_day(now.date()), args.state_root, args.root)
    print(json.dumps(result))


if __name__ == '__main__': main()
