"""Read original published hedge receipts for the strict native handoff bridge.

Only the existing EM/Sina 000852 contract is supported. No values are rounded or
rewritten here; the native reader validates every original source object.
"""
import base64
import csv
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import zipfile

from prepare_microcap_local import atomic, encode, sha

REPO='liuruojiang/codex-daily-automation-probe'
WORKFLOW='.github/workflows/microcap-member-daily.yml'
RUNTIME='4774966da38f0b5af18617f73cc67c39c9e41a8e'
PANEL='outputs/microcap_top100_mom16_biweekly_live_v2_0_base_panel_refreshed.csv'
REPORT='microcap-member-daily-report'
LIMIT=64*1024*1024
DATA=Path(__file__).resolve().parents[1]/'data/microcap_handoff_sources'


def blob(raw):return dict(sha256=sha(raw),base64=base64.b64encode(raw).decode('ascii'))


def api(path):
    value=subprocess.run(['gh','api',path],capture_output=True,text=True,encoding='utf-8',timeout=120)
    if value.returncode:raise RuntimeError('Published hedge witness metadata unavailable: '+path)
    return json.loads(value.stdout)


def entries(path,key):
    for page in range(1,101):
        value=api(path+('&' if '?' in path else '?')+f'per_page=100&page={page}')[key]
        if not isinstance(value,list):raise ValueError('Invalid witness metadata collection')
        yield from value
        if len(value)<100:return
    raise ValueError('Incomplete witness metadata pagination')


def original_report(item,out):
    digest=item.get('digest','')
    if re.fullmatch('sha256:[0-9a-f]{64}',digest) is None:raise ValueError('Witness artifact lacks its original SHA256')
    if not 0<item.get('size_in_bytes',0)<=LIMIT:raise ValueError('Witness artifact exceeds its budget')
    path=out/f"report-{item['id']}.zip"
    if not path.exists():
        temporary=path.with_suffix('.partial')
        if temporary.exists():raise ValueError('Preserve interrupted witness download; inspect before retry')
        with temporary.open('xb') as stream:
            process=subprocess.run(['gh','api',f"repos/{REPO}/actions/artifacts/{item['id']}/zip"],stdout=stream,stderr=subprocess.PIPE,timeout=180)
        if process.returncode:raise RuntimeError('Original hedge witness download failed')
        if temporary.stat().st_size>LIMIT or sha(temporary.read_bytes())!=digest[7:]:raise ValueError('Original witness download hash differs')
        temporary.replace(path)
    if sha(path.read_bytes())!=digest[7:]:raise ValueError('Cached original witness artifact changed')
    wanted=('daily-inputs/hedge.json','daily-inputs/hedge_daily_bar.csv',
            'daily-result/checkpoint.json','daily-result/history.csv')
    with zipfile.ZipFile(path) as archive:
        if sum(i.file_size for i in archive.infolist())>LIMIT:raise ValueError('Witness expands beyond its budget')
        result={}
        for suffix in wanted:
            names=[n for n in archive.namelist() if n==suffix or n.endswith('/'+suffix)]
            if len(names)!=1:raise ValueError('Original witness role absent or ambiguous: '+suffix)
            p=PurePosixPath(names[0])
            if p.is_absolute() or '..' in p.parts or '\\' in names[0]:raise ValueError('Unsafe original witness path')
            result[suffix]=archive.read(names[0])
    return result


def published(day,out,expected):
    base=f'repos/{REPO}/actions';matches=[]
    for item in entries(base+'/artifacts?name='+REPORT,'artifacts'):
        if item.get('name')!=REPORT or item.get('expired') is not False:continue
        stamp=datetime.fromisoformat(item['created_at'].replace('Z','+00:00')).astimezone(timezone(timedelta(hours=8)))
        if stamp.date().isoformat()!=day:continue
        run_id=item['workflow_run']['id'];run=api(base+f'/runs/{run_id}')
        if (run.get('status')!='completed' or run.get('conclusion')!='success'
                or run.get('head_branch')!='main' or run.get('path')!=WORKFLOW
                or run.get('repository',{}).get('full_name')!=REPO
                or run.get('head_repository',{}).get('full_name')!=REPO):continue
        cfg=api(f'repos/{REPO}/contents/config/microcap_member_runtime.json?ref='+run['head_sha'])
        code=json.loads(base64.b64decode(cfg['content']))
        if code.get('strategy_sha')!=RUNTIME:raise ValueError('Witness was not produced by the approved daily reader')
        jobs=list(entries(base+f'/runs/{run_id}/jobs','jobs'))
        if not any(j.get('conclusion')=='success' and any(s.get('name')=='Publish verified member checkpoint'
                   and s.get('conclusion')=='success' for s in j.get('steps',[])) for j in jobs):continue
        raw=original_report(item,out);cp=json.loads(raw['daily-result/checkpoint.json'])
        history=raw['daily-result/history.csv']
        if cp.get('as_of_date')!=day or cp.get('history',{}).get('sha256')!=sha(history):raise ValueError('Witness checkpoint history identity differs')
        rows=list(csv.DictReader(io.StringIO(history.decode('utf-8-sig'))))
        if not rows or rows[-1]['date']!=day:raise ValueError('Witness close date differs')
        row=rows[-1]
        if any(Decimal(row[k])!=Decimal(expected[k]) for k in ('microcap','hedge')):
            raise ValueError('Conflicting published daily close; not a precision bridge')
        quote=json.loads(raw['daily-inputs/hedge.json'])
        if quote.get('source')!='eastmoney':raise ValueError('Unapproved earned hedge source; retain exact handoff')
        matches.append((item['id'],raw,dict(run_id=run_id,artifact_id=item['id'],artifact_digest=item['digest'],head_sha=run['head_sha'])))
    if not matches:raise ValueError('No verified original daily hedge witness: '+day)
    _,raw,origin=max(matches,key=lambda x:x[0])
    atomic(out/(day+'-origin.json'),encode(origin))
    return dict(provider='eastmoney',instrument='1.000852',
                hedge_json=blob(raw['daily-inputs/hedge.json']),bar_csv=blob(raw['daily-inputs/hedge_daily_bar.csv']))


def rows(cp):
    spec=cp['history'];raw=(cp['_directory']/spec['path']).read_bytes()
    if sha(raw)!=spec['sha256']:raise ValueError('Handoff history changed before source reconciliation')
    return list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))


def build(consumer,current_path,candidate_path,out,logs=(),fetch=published):
    consumer,current_path,candidate_path,out=map(Path,(consumer,current_path,candidate_path,out))
    out.mkdir(parents=True,exist_ok=True)
    current=json.loads(current_path.read_bytes());candidate=json.loads(candidate_path.read_bytes())
    for cp,path in ((current,current_path),(candidate,candidate_path)):cp['_directory']=path.parent
    old,new=rows(current),rows(candidate)
    if len(new) not in (len(old),len(old)+1):raise ValueError('Precision reconciliation cannot skip earned sessions')
    current_period=json.loads((current_path.parent/current['period']['path']).read_bytes())
    period_raw=(candidate_path.parent/candidate['period']['path']).read_bytes()
    if sha(period_raw)!=candidate['period']['sha256']:raise ValueError('Formal period changed')
    period=json.loads(period_raw)
    panel=(consumer/PANEL).read_bytes();manifest=(consumer/'outputs/top100_delivery_manifest.json').read_bytes()
    formal_manifest=json.loads(manifest)
    if (candidate.get('bootstrap_source')!='existing_formal_exports' or formal_manifest.get('ok') is not True
            or formal_manifest.get('status')!='complete' or formal_manifest.get('scope')!='whole_workspace_delivery'
            or formal_manifest.get('expected_date')!=candidate['as_of_date']):
        raise ValueError('Hedge witness requires a completed formal candidate')
    if sha(panel)!=period['source_files'][PANEL] or sha(manifest)!=period['source_manifest_sha256']:
        raise ValueError('Formal hedge panel/manifest provenance differs')
    panel_rows={r['date'][:10]:r for r in csv.DictReader(io.StringIO(panel.decode('utf-8-sig')))}
    known={r['date']:r for r in current_period.get('hedge_precision_bridge_v1',{}).get('records',[])}
    records=[]
    for earned,formal in zip(old,new):
        if earned['date']!=formal['date'] or Decimal(earned['microcap'])!=Decimal(formal['microcap']):
            raise ValueError('Not a hedge-only representation difference')
        if Decimal(earned['hedge'])==Decimal(formal['hedge']) or earned['date'] in known:continue
        day=earned['date'];source=None
        if Decimal(panel_rows[day]['1.000852'])!=Decimal(formal['hedge']):raise ValueError('Formal history is not its original index panel')
        for path in logs:
            raw=Path(path).read_bytes()
            for line in raw.decode('utf-8-sig').splitlines():
                if '[index-history] ' not in line:continue
                attrs=json.loads(line.split('[index-history] ',1)[1])
                if (attrs.get('independent_history_source')=='sina_legacy'
                        and attrs.get('independent_history_start','')<=day<=attrs.get('independent_history_end','')):source=raw
        if source is None:
            registry=json.loads((DATA/'registry.json').read_bytes())
            entry=next((x for x in registry['records'] if x['date']==day and Decimal(x['close'])==Decimal(formal['hedge'])),None)
            if entry:
                source=(DATA/entry['log']).read_bytes()
                if sha(source)!=entry['sha256']:raise ValueError('Archived formal source receipt changed')
        if source is None:raise ValueError('Original formal hedge source is unavailable: '+day)
        records.append(dict(date=day,earned=fetch(day,out,earned),formal=dict(provider='sina_legacy',instrument='sh000852',
            close=formal['hedge'],manifest_sha256=sha(manifest),panel_sha256=sha(panel),source_log=blob(source))))
    if not records and not known:return None
    witness=dict(schema='member_hedge_precision_witness_v1',current_checkpoint_sha256=sha(current_path.read_bytes()),
                 candidate_checkpoint_sha256=sha(candidate_path.read_bytes()),records=records)
    path=out/'witness.json';atomic(path,encode(witness));return path
