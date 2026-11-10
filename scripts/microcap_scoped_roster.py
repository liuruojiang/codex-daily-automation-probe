"""Carry an original weekly screen across hosts without changing its evidence."""
import argparse
import csv
import io
import json
from pathlib import Path
import zipfile
from prepare_microcap_local import atomic,encode,sha,fetch_snapshot,select_pool
from maintain_microcap_local import load_pool,bound

SCHEMA='microcap_portable_weekly_roster_v1'
LIMIT=10*1024*1024


def export(pool,target,bundle):
    pool=Path(pool);summary,symbols,_=load_pool(pool,target)
    run=Path(summary['run_directory']);source=pool.parent/'source-v2-name'
    if not source.exists():source=pool.parent/'source'
    count=json.loads((source/'page-01.json').read_bytes())['result']['pages']
    names=[f'page-{i:02d}{suffix}' for i in range(1,count+1) for suffix in ('.json','.receipt.json')]
    bodies={'original-summary.json':pool.read_bytes(),'candidates.json':(run/'candidates.json').read_bytes(),
            'st-reference.csv':(run/'st-reference.csv').read_bytes()}
    bodies.update({'source/'+n:(source/n).read_bytes() for n in names})
    rows=fetch_snapshot(source,summary['date'],get=lambda *a,**k:(_ for _ in ()).throw(ValueError('Missing original screen page')))
    candidates=json.loads(bodies['candidates.json'])
    members=[r['symbol'] for r in candidates['candidates'] if r['current_member']]
    st={r['code'] for r in csv.DictReader(io.StringIO(bodies['st-reference.csv'].decode('utf-8-sig')))}
    if select_pool(rows,members,st)!=candidates or sha(bodies['st-reference.csv'])!=summary['st_reference_sha256']:
        raise ValueError('Original screen cannot reproduce its weekly roster')
    manifest=dict(schema=SCHEMA,date=summary['date'],files={n:sha(raw) for n,raw in bodies.items()},
                  original_observation_clocks_preserved=True,final_strategy_delivery=False)
    bundle=Path(bundle);bundle.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(bundle,'x',zipfile.ZIP_DEFLATED) as archive:
        for name,raw in bodies.items():archive.writestr(name,raw)
        archive.writestr('manifest.json',encode(manifest))
    return dict(status='original_weekly_roster_exported',sha256=sha(bundle.read_bytes()),date=summary['date'])


def restore(bundle,expected,target,out):
    bundle=Path(bundle);raw=bundle.read_bytes()
    if len(raw)>LIMIT or sha(raw)!=expected:raise ValueError('Weekly roster archive identity differs')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names=archive.namelist();manifest=json.loads(archive.read('manifest.json'))
        if (manifest.get('schema')!=SCHEMA or manifest.get('original_observation_clocks_preserved') is not True
                or manifest.get('final_strategy_delivery') is not False
                or len(names)!=len(set(names)) or set(names)!={'manifest.json',*manifest['files']}
                or sum(v.file_size for v in archive.infolist())>LIMIT):raise ValueError('Invalid weekly roster archive')
        bodies={name:archive.read(name) for name in manifest['files']}
    out=Path(out).resolve()
    if out.exists():raise ValueError('Weekly roster destination already exists')
    for name,body in bodies.items():
        allowed=name in ('original-summary.json','candidates.json','st-reference.csv') or (
            name.startswith('source/page-') and name.endswith(('.json','.receipt.json')) and len(Path(name).parts)==2)
        if not allowed or sha(body)!=manifest['files'][name]:raise ValueError('Weekly roster source identity differs')
        bound(out,name)
    summary=json.loads(bodies['original-summary.json'])
    if (summary['date']!=manifest['date'] or sha(bodies['candidates.json'])!=summary['candidates_sha256']
            or sha(bodies['st-reference.csv'])!=summary['st_reference_sha256']):
        raise ValueError('Weekly roster original summary differs')
    out.mkdir(parents=True)
    for name,body in bodies.items():atomic(bound(out,name),body)
    # A derived locator changes only its host path. Keep the original receipt
    # byte-for-byte beside it and bind the translation explicitly.
    portable=dict(summary,run_directory=str(out),portable_original_summary_sha256=sha(bodies['original-summary.json']))
    path=out/'latest.json'
    load_pool(out/'original-summary.json',target,run_directory=out)
    rows=fetch_snapshot(out/'source',summary['date'],get=lambda *a,**k:(_ for _ in ()).throw(ValueError('Missing original screen page')))
    candidates=json.loads(bodies['candidates.json'])
    members=[r['symbol'] for r in candidates['candidates'] if r['current_member']]
    st={r['code'] for r in csv.DictReader(io.StringIO(bodies['st-reference.csv'].decode('utf-8-sig')))}
    if select_pool(rows,members,st)!=candidates:raise ValueError('Restored original weekly ranking differs')
    atomic(out/'transport-receipt.json',encode(dict(status='original_weekly_roster_restored',bundle_sha256=expected,
        original_summary_sha256=sha(bodies['original-summary.json']),original_observation_clocks_preserved=True)))
    atomic(path,encode(portable))
    return dict(status='original_weekly_roster_restored',pool=str(path),date=summary['date'])


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('export','restore'))
    p.add_argument('--bundle',type=Path,required=True);p.add_argument('--date',required=True)
    p.add_argument('--pool',type=Path);p.add_argument('--sha256');p.add_argument('--out',type=Path);a=p.parse_args()
    if a.action=='export':
        if a.pool is None:p.error('export requires --pool')
        result=export(a.pool,a.date,a.bundle)
    else:
        if not a.sha256 or a.out is None:p.error('restore requires --sha256 and --out')
        result=restore(a.bundle,a.sha256,a.date,a.out)
    print(json.dumps(result))

if __name__=='__main__':main()
