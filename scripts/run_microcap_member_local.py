"""Local member-only preparation/reporting; no SMTP or whole-state operations."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo


BJ=ZoneInfo('Asia/Shanghai')


def validate_local_window(day: str, prepare_only: bool, clock=None):
    now=clock or datetime.now(BJ)
    if now.tzinfo is None or day!=now.astimezone(BJ).date().isoformat():
        raise ValueError('Local member report must use the actual Beijing date')
    if not prepare_only and now.astimezone(BJ).hour>=15:
        raise ValueError('No new local intraday collection at or after 15:00 Beijing')


def github_environment():
    env=os.environ.copy()
    if not env.get('GITHUB_TOKEN'):
        # Capture only; credential bytes never enter argv, stdout, logs or files.
        process=subprocess.run(['gh','auth','token'],capture_output=True,text=True,check=False)
        if process.returncode!=0 or not process.stdout.strip():
            raise RuntimeError('GitHub authentication unavailable for member checkpoint read')
        env['GITHUB_TOKEN']=process.stdout.strip()
    env.setdefault('GITHUB_REPOSITORY','liuruojiang/codex-daily-automation-probe')
    env['PYTHONDONTWRITEBYTECODE']='1'
    return env


def reusable_checkpoint(path, policy):
    if path is None:return None
    value=json.loads(Path(path).read_bytes())
    if value.get('history',{}).get('sha256') in policy.get('superseded_history_sha256',[]):return None
    return Path(path).resolve()


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--date',required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--prepare-only',action='store_true')
    p.add_argument('--checkpoint',type=Path,help='Reuse a verified morning checkpoint without downloading it again')
    p.add_argument('--automation-root',type=Path,default=Path(__file__).resolve().parents[1])
    args=p.parse_args()
    validate_local_window(args.date,args.prepare_only)
    auto=args.automation_root.resolve();strategy=args.root.resolve();out=args.out.resolve()
    if out==strategy or out.is_relative_to(strategy/'outputs'):
        raise ValueError('Local reporting cannot overwrite production outputs')
    out.mkdir(parents=True,exist_ok=False)
    runtime=json.loads((auto/'config/microcap_member_runtime.json').read_bytes())['strategy_sha']
    actual=subprocess.run(['git','-C',str(strategy),'rev-parse','HEAD'],capture_output=True,text=True,check=True).stdout.strip()
    if actual!=runtime:
        raise ValueError('Local member runtime differs from the pinned strategy commit')
    commands=[]
    env=os.environ.copy();env['PYTHONDONTWRITEBYTECODE']='1'
    def run(command, child_env=env):
        started=datetime.now(BJ).isoformat()
        process=subprocess.run(command,env=child_env,capture_output=True,text=True,encoding='utf8',errors='replace')
        number=len(commands)+1
        # The recovery helper does not render request headers or credentials.
        (out/f'{number:02d}.stdout.log').write_text(process.stdout,encoding='utf8')
        (out/f'{number:02d}.stderr.log').write_text(process.stderr,encoding='utf8')
        commands.append(dict(command=command,started_at=started,completed_at=datetime.now(BJ).isoformat(),exit_code=process.returncode))
        (out/'commands.json').write_text(json.dumps(commands,ensure_ascii=False,indent=2),encoding='utf8')
        if process.returncode:
            raise RuntimeError(f'Member local stage {number} failed; see saved exit code and logs')
    py=sys.executable
    seed_policy=json.loads((auto/'config/microcap_member_checkpoint.json').read_bytes())
    cached=reusable_checkpoint(args.checkpoint,seed_policy)
    if args.checkpoint is not None and cached is None:
        (out/'superseded_checkpoint.json').write_text(json.dumps({'cached_checkpoint':str(args.checkpoint),'reason':'exact superseded history serialization; original retained; use reviewed small seed'}),encoding='utf8')
    if cached is None:
        sys.path.insert(0,str(strategy))
        from scripts.exchange_calendar import latest_completed_session
        not_after=latest_completed_session().isoformat()
        run([py,'-B',str(auto/'scripts/restore_microcap_member_checkpoint.py'),'--strategy-sha',runtime,'--seed-config',str(auto/'config/microcap_member_checkpoint.json'),'--not-after-date',not_after,'--out',str(out/'checkpoint')],github_environment())
        cp=out/'checkpoint/checkpoint.json'
    else:
        cp=cached
    run([py,'-B',str(strategy/'scripts/member_period_handoff.py'),'verify','--checkpoint',str(cp)])
    if args.prepare_only:
        print(json.dumps({'status':'prepared','checkpoint':str(cp),'market_quotes_collected':False}))
        return 0
    validate_local_window(args.date,False)
    run([py,'-B',str(strategy/'scripts/collect_member_daily_inputs.py'),'--root',str(strategy),'--checkpoint',str(cp),'--date',args.date,'--mode','intraday','--out',str(out/'inputs')])
    run([py,'-B',str(strategy/'scripts/member_daily_signal.py'),'--root',str(strategy),'--checkpoint',str(cp),'--quotes',str(out/'inputs/quotes.json'),'--hedge',str(out/'inputs/hedge.json'),'--date',args.date,'--mode','intraday','--out',str(out/'result')])
    run([py,'-B',str(auto/'scripts/build_microcap_member_digest.py'),'--result',str(out/'result/result.json'),'--context-checkpoint',str(cp),'--expected-date',args.date,'--out',str(out/'report')])
    # A completed intraday calculation must still be published before cutoff.
    validate_local_window(args.date,False)
    print(json.dumps({'status':'report_ready_not_published','report':str(out/'report/report.md'),'result':str(out/'result/result.json'),'smtp':False}))
    return 0


if __name__=='__main__':raise SystemExit(main())
