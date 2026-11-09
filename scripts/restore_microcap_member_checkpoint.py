"""Recover only a published small member checkpoint; never fall back to a whole state."""
from __future__ import annotations
import argparse
import csv
from datetime import date, datetime
from decimal import Decimal, DecimalException
import io
import hashlib
from http.client import IncompleteRead
import json
import math
import os
import re
import stat
import sys
from time import sleep
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from restore_microcap_verified_state import api_request, download
from restore_microcap_verified_state import StripCrossOriginAuthRedirectHandler
from build_microcap_member_digest import REVISIONS

WORKFLOWS = {".github/workflows/microcap-member-daily.yml", ".github/workflows/microcap-member-bootstrap.yml",
             ".github/workflows/microcap-member-maintenance.yml"}
MAX_BYTES = 64 * 1024 * 1024
V2_PREFIX = 'microcap-member-checkpoint-v2-'
MAX_LEGACY_READS = 8


def retry_checkpoint_read(read, label, *, pause=None):
    """Retry only the failed idempotent read; never select an older state."""
    for attempt in range(3):
        try:
            return read()
        except urllib.error.HTTPError as exc:
            # Permission/rate-limit responses and missing objects require an
            # explicit diagnosis. They must not become a seed/older-state read.
            if exc.code not in {408, 500, 502, 503, 504} or attempt == 2:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, IncompleteRead):
            if attempt == 2:
                raise
        print(f'Retrying {label} after transient transport failure ({attempt + 1}/2)', file=sys.stderr)
        (sleep if pause is None else pause)(2 ** attempt)


def checkpoint_json(request):
    def read():
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    return retry_checkpoint_read(read, 'checkpoint metadata request')


def _semantic(value):
    """Canonical scalar representation, with no numerical tolerance."""
    if isinstance(value,dict):return {k:_semantic(v) for k,v in sorted(value.items())}
    if isinstance(value,list):return [_semantic(v) for v in value]
    if value is None or value=='':return None
    if isinstance(value,bool):return value
    if isinstance(value,str) and value.lower() in ('true','false'):return value.lower()=='true'
    if isinstance(value,str) and value[:1] in ('{','['):
        try:return _semantic(json.loads(value))
        except ValueError:pass
    try:
        number=Decimal(str(value))
        if number.is_nan():return None
        return {'number':str(number.normalize())}
    except DecimalException:return value


def _turnover_semantic(raw):
    events=list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
    for event in events:
        for key in ('rebalance_date','constraint_trade_date','execution_date','effective_date','return_start_date'):
            value=event.get(key)
            if value:
                stamp=datetime.fromisoformat(value)
                if stamp.tzinfo is not None or any((stamp.hour,stamp.minute,stamp.second,stamp.microsecond)):
                    raise ValueError('Invalid daily turnover event date')
                event[key]=stamp.date().isoformat()
    return _semantic(events)


def checkpoint_identity(raw):
    """Read verified transport metadata without extracting any candidate."""
    if len(raw) > MAX_BYTES:
        raise ValueError('Member checkpoint archive exceeds budget')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        if len(infos) > 32 or sum(x.file_size for x in infos) > MAX_BYTES:
            raise ValueError('Member checkpoint payload exceeds budget')
        names = set()
        for item in infos:
            p=PurePosixPath(item.filename)
            if (p.is_absolute() or '..' in p.parts or '\\' in item.filename or ':' in item.filename or any(x.endswith(('.', ' ')) for x in p.parts)
                    or item.filename.casefold() in names or stat.S_ISLNK(item.external_attr >> 16)):
                raise ValueError('Unsafe member checkpoint archive path')
            names.add(item.filename.casefold())
        cp=json.loads(archive.read('checkpoint.json'))
        if cp.get('schema')!='member_daily_checkpoint_v1':
            raise ValueError('Unpublishable checkpoint schema')
        anchor=date.fromisoformat(cp['as_of_date']).isoformat()
        next_day=cp.get('next_trade_date')
        if next_day is not None:
            if (not isinstance(next_day,str) or date.fromisoformat(next_day).isoformat()!=next_day
                    or next_day<=anchor):
                raise ValueError('Checkpoint next execution date must be canonical and after its close')
        payload={}
        for key in ('period','history','turnover','version_state','prices'):
            spec=cp[key];p=PurePosixPath(spec['path'])
            if p.is_absolute() or '..' in p.parts or '\\' in spec['path'] or ':' in spec['path']:
                raise ValueError('Checkpoint dependency path escapes archive')
            data=archive.read(spec['path'])
            if hashlib.sha256(data).hexdigest()!=spec['sha256']:
                raise ValueError('Checkpoint dependency SHA mismatch')
            payload[key]=data
        period=json.loads(payload['period']);members=period['effective_members']
        symbols=[m['symbol'] for m in members]
        if (period.get('schema')!='member_period_v1' or len(symbols)!=100 or len(set(symbols))!=100
                or any(re.fullmatch(r'\d{6}',s,flags=re.ASCII) is None for s in symbols)
                or [int(m['rank']) for m in members]!=list(range(1,101))):
            raise ValueError('Checkpoint member identity invalid')
        state=cp.get('period_status','current')
        if state not in ('current','awaiting_handoff'):
            raise ValueError('Unpublishable period state')
        history=list(csv.DictReader(io.StringIO(payload['history'].decode('utf-8-sig'))))
        days=[date.fromisoformat(x['date'][:10]).isoformat() for x in history]
        if not days or days!=sorted(set(days)) or days[-1]!=anchor:
            raise ValueError('Checkpoint history date invalid')
        semantic=[]
        for row in history:
            values=[Decimal(row[k]) for k in ('microcap','hedge')]
            if any(not v.is_finite() or v<=0 for v in values):
                raise ValueError('Checkpoint history price invalid')
            semantic.append((row['date'][:10],*[str(v.normalize()) for v in values]))
        prices=json.loads(payload['prices']);basis=prices['rows']
        if (prices.get('schema')!='member_price_basis_v1' or prices.get('as_of_date')!=anchor or sorted(r['symbol'] for r in basis)!=sorted(symbols)
                or any(r['previous_raw_date']!=anchor or not math.isfinite(float(r['previous_raw_close'])) or float(r['previous_raw_close'])<=0 for r in basis)):
            raise ValueError('Checkpoint price basis invalid')
        versions=json.loads(payload['version_state'])['versions']
        if set(versions)!=set(REVISIONS) or any(str(r['date'])[:10]!=anchor or r.get('strategy_revision')!=REVISIONS[v] for v,r in versions.items()):
            raise ValueError('Checkpoint version-state date invalid')
        raw_basis=sorted([[r['symbol'],r['previous_raw_date'],_semantic(r['previous_raw_close'])] for r in basis])
        economic_period={key:period.get(key) for key in ('anchor_day','effective_from','effective_members','target_members','rebalance_events')}
        # A different execution clock is a conflicting state, even when every
        # member, price and model value agrees. Missing legacy clocks stay None.
        fingerprint=hashlib.sha256(json.dumps([semantic,_semantic(economic_period),raw_basis,_semantic(versions),next_day,
                            _turnover_semantic(payload['turnover'])],sort_keys=True).encode()).hexdigest()
        return dict(as_of_date=anchor,period_status=state,period_sha=cp['period']['sha256'],fingerprint=fingerprint)


def checkpoint_artifact_name(path, sha):
    if re.fullmatch('[0-9a-f]{40}',sha) is None:
        raise ValueError('Exact runtime SHA required')
    path=Path(path);cp=json.loads(path.read_bytes())
    date.fromisoformat(cp['as_of_date'])
    phase=cp.get('period_status','current')
    if phase not in ('current','awaiting_handoff') or re.fullmatch('[0-9a-f]{64}',cp['period']['sha256']) is None:
        raise ValueError('Publishable checkpoint phase and period hash required')
    return f"{V2_PREFIX}{sha}-{cp['as_of_date']}-{phase}-{cp['period']['sha256'][:12]}"


def unpack(raw: bytes, out: Path):
    if len(raw) > MAX_BYTES:
        raise ValueError("Member checkpoint archive exceeds small-package budget")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        if len(infos) > 32 or sum(x.file_size for x in infos) > MAX_BYTES:
            raise ValueError("Member checkpoint payload exceeds budget")
        seen = set()
        for item in infos:
            name = PurePosixPath(item.filename)
            if (name.is_absolute() or ".." in name.parts or "\\" in item.filename or ":" in item.filename
                    or any(part.endswith(('.', ' ')) for part in name.parts)
                    or item.filename.casefold() in seen or stat.S_ISLNK(item.external_attr >> 16)):
                raise ValueError("Unsafe member checkpoint archive path")
            seen.add(item.filename.casefold())
        if "checkpoint.json" not in seen:
            raise ValueError("Member checkpoint root is missing")
        out.mkdir(parents=True, exist_ok=False)
        for item in infos:
            target = out.joinpath(*PurePosixPath(item.filename).parts)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as stream:
                    stream.write(archive.read(item))


def fetch_latest(repository: str, token: str, sha: str, not_after_date=None):
    if re.fullmatch(r"[0-9a-f]{40}", sha) is None:
        raise ValueError("Member checkpoint requires exact strategy SHA")
    ceiling=date.fromisoformat(not_after_date).isoformat() if not_after_date else None
    base=f"https://api.github.com/repos/{repository}/actions"
    v2=re.compile(re.escape(V2_PREFIX+sha)+r"-(\d{4}-\d{2}-\d{2})-(current|awaiting_handoff)-([0-9a-f]{12})$")
    old_name="microcap-member-checkpoint-v1-"+sha
    modern=[];legacy=[]
    for page in range(1,101):
        items=checkpoint_json(api_request(f"{base}/artifacts?per_page=100&page={page}",token))["artifacts"]
        for item in items:
            if item.get('expired'):continue
            match=v2.fullmatch(item.get('name',''))
            if match:
                session,phase,period=match.groups()
                date.fromisoformat(session)
                if ceiling and session>ceiling:continue
                item['_declared']=dict(as_of_date=session,period_status=phase,period_short=period)
                modern.append(item)
            elif item.get('name')==old_name:legacy.append(item)
        if len(items)<100:break
    else:raise RuntimeError('Member artifact pagination exceeded safety limit')
    modern.sort(key=lambda a:(a['_declared']['as_of_date'],a['_declared']['period_status']=='current',int(a['id'])),reverse=True)
    if not modern and len(legacy)>MAX_LEGACY_READS:
        raise RuntimeError('Legacy checkpoint order is ambiguous; publish dated v2 checkpoints rather than guessing from upload order')
    candidates=modern+(legacy if len(legacy)<=MAX_LEGACY_READS else [])
    accepted=[];rejected=[]
    best_key=None
    for artifact in candidates:
        declared=artifact.get('_declared')
        if best_key is not None and declared is None and modern:break
        key=(declared['as_of_date'],declared['period_status']=='current') if declared else None
        if best_key is not None and key is not None and key<best_key:break
        run_id=artifact['workflow_run']['id']
        run=checkpoint_json(api_request(f"{base}/runs/{run_id}",token))
        if run.get('head_branch')!='main' or run.get('status')!='completed' or run.get('path','').split('@',1)[0] not in WORKFLOWS:continue
        jobs=checkpoint_json(api_request(f"{base}/runs/{run_id}/jobs?per_page=100",token))['jobs']
        if not any(any(s.get('name')=='Publish verified member checkpoint' and s.get('conclusion')=='success' for s in j.get('steps',[])) for j in jobs):continue
        if artifact.get('archive_download_url')!=f"{base}/artifacts/{artifact['id']}/zip":
            raise ValueError('Unexpected member checkpoint download URL')
        # Exhausted transport failures propagate; they never downgrade state.
        # Only a malformed payload is rejected in favour of a verified one.
        raw=retry_checkpoint_read(lambda: download(artifact,token), 'checkpoint archive request')
        try:
            identity=checkpoint_identity(raw)
            if ceiling and identity['as_of_date']>ceiling:raise ValueError('Checkpoint is later than permitted market session')
            if declared and (any(identity[k]!=declared[k] for k in ('as_of_date','period_status')) or not identity['period_sha'].startswith(declared['period_short'])):
                raise ValueError('Artifact date/phase/period name differs from verified payload')
        except (ValueError,KeyError,TypeError,DecimalException,zipfile.BadZipFile) as exc:
            rejected.append(dict(artifact_id=artifact['id'],reason=str(exc)))
            print('Rejected malformed checkpoint: '+json.dumps(rejected[-1]),file=sys.stderr)
            continue
        semantic_key=(identity['as_of_date'],identity['period_status']=='current')
        if best_key is None or semantic_key>best_key:
            accepted=[];best_key=semantic_key
        if semantic_key==best_key:
            accepted.append((artifact,identity,raw))
    if accepted:
        if len({x[1]['fingerprint'] for x in accepted})!=1:
            raise RuntimeError('Conflicting published checkpoint states share the same date and handoff phase; explicit reconciliation required')
        artifact,identity,raw=max(accepted,key=lambda x:int(x[0]['id']))
        return dict(artifact,_payload=raw,_selection=identity,_rejected=rejected)
    if len(legacy)>MAX_LEGACY_READS:raise RuntimeError('Legacy checkpoint order is ambiguous; publish dated v2 checkpoints rather than guessing from upload order')
    if rejected:raise RuntimeError('No valid published checkpoint remains after explicit payload rejection: '+json.dumps(rejected))
    raise RuntimeError('No published member checkpoint: run the explicit one-time bootstrap; whole-state fallback is disabled')


def seed_download(path: Path, sha: str) -> bytes:
    config = json.loads(path.read_bytes())
    url = config.get("url", "")
    expected = config.get("sha256", "")
    if (config.get("schema") != "member_daily_seed_v1" or config.get("strategy_sha") != sha
            or not url.startswith("https://github.com/liuruojiang/microcap/releases/download/")
            or re.fullmatch(r"[0-9a-f]{64}", expected) is None):
        raise ValueError("Member seed config identity is not approved")
    bundled = config.get('bundled_path')
    if bundled is not None:
        if not isinstance(bundled, str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*\.zip', bundled) is None:
            raise ValueError('Invalid bundled seed path')
        source = path.resolve().parent / bundled
        if source.resolve().parent != path.resolve().parent:
            raise ValueError('Invalid bundled seed path')
        # The reviewed bytes travel with the exact configuration/checkout. A
        # missing or corrupt bundle is an integrity failure, not a reason to
        # silently replace it with an unrelated checkpoint or remote response.
        with source.open('rb') as stream:
            raw = stream.read(MAX_BYTES + 1)
    else:
        opener = urllib.request.build_opener(StripCrossOriginAuthRedirectHandler())
        for attempt in range(3):
            try:
                with opener.open(url, timeout=60) as response:
                    raw = response.read(MAX_BYTES + 1)
                break
            except urllib.error.HTTPError as exc:
                if exc.code not in {408, 429, 500, 502, 503, 504} or attempt == 2:
                    raise
            except (urllib.error.URLError, TimeoutError, ConnectionError, IncompleteRead):
                if attempt == 2:
                    raise
            print(f'Retrying reviewed seed download after transient transport failure ({attempt + 1}/2)', file=sys.stderr)
            sleep(2 ** attempt)
    if len(raw) > MAX_BYTES or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("Member seed archive size/SHA mismatch")
    return raw


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path)
    p.add_argument("--strategy-sha", required=True)
    p.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    p.add_argument("--seed-config", type=Path)
    p.add_argument("--not-after-date")
    p.add_argument("--describe-checkpoint", type=Path)
    args = p.parse_args()
    if args.describe_checkpoint:
        print(checkpoint_artifact_name(args.describe_checkpoint,args.strategy_sha))
        return
    if args.out is None:
        p.error('--out is required for checkpoint restoration')
    token = os.environ["GITHUB_TOKEN"]
    try:
        artifact = fetch_latest(args.repository, token, args.strategy_sha,args.not_after_date)
    except RuntimeError as exc:
        if not str(exc).startswith("No published member checkpoint:") or args.seed_config is None:
            raise
        raw=seed_download(args.seed_config,args.strategy_sha)
        identity=checkpoint_identity(raw)
        if args.not_after_date and identity['as_of_date']>date.fromisoformat(args.not_after_date).isoformat():
            raise ValueError('Approved seed is later than the permitted market session')
        unpack(raw, args.out)
        print(json.dumps({"source": "exact_small_bootstrap_seed", "strategy_sha": args.strategy_sha}))
    else:
        unpack(artifact["_payload"], args.out)
        print(json.dumps({"artifact_id": artifact["id"], "run_id": artifact["workflow_run"]["id"], "strategy_sha": args.strategy_sha, "selection": artifact["_selection"], "rejected": artifact["_rejected"]}))


if __name__ == "__main__":
    main()
