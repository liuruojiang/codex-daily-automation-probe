"""Restore a reviewed immutable report for send-only recovery, without fetching prices."""
from __future__ import annotations
import argparse
import csv
import hashlib
import io
import json
import urllib.request
import zipfile
from pathlib import Path
from restore_microcap_member_checkpoint import MAX_BYTES, unpack
from restore_microcap_verified_state import StripCrossOriginAuthRedirectHandler
from build_microcap_member_digest import render


def verify(out: Path, sha: str, day: str):
    manifest=json.loads((out/'manifest.json').read_bytes())
    if (manifest.get('schema') != 'member_daily_saved_report_v1'
            or manifest.get('strategy_sha') != sha or manifest.get('market_date') != day):
        raise ValueError('Saved member report source/date identity differs')
    files=manifest.get('files')
    observed={p.relative_to(out).as_posix() for p in out.rglob('*') if p.is_file()} - {'manifest.json'}
    if not isinstance(files,dict) or set(files) != observed:
        raise ValueError('Saved report closure differs from manifest')
    for name,digest in files.items():
        if hashlib.sha256((out/name).read_bytes()).hexdigest()!=digest:
            raise ValueError('Saved report file SHA mismatch: '+name)
    result=json.loads((out/'result.json').read_bytes())
    if result.get('publication_mode')!='close_confirmed':
        raise ValueError('Saved report recovery is close-confirmed only')
    meta=render(result,expected_date=day)
    if meta['status']!='OK' or any(name not in observed for name in ('v2_0_signal.csv','v2_3_signal.csv','v2_5_signal.csv','quotes.json','hedge.json')):
        raise ValueError('Reviewed normal send recovery requires all final signals and source snapshots')
    for item in result['versions']:
        name=item['version'].replace('.','_')+'_signal.csv'
        with (out/name).open(encoding='utf-8-sig',newline='') as stream:rows=list(csv.DictReader(stream))
        if len(rows)!=1:
            raise ValueError('Saved signal CSV must have one row')
        row, signal=rows[0],item['signal']
        for field in ('strategy_revision','current_holding','next_holding','trade_state'):
            if row.get(field)!=str(signal.get(field)):
                raise ValueError('Saved CSV and result conflict: '+field)
        for field in ('current_execution_scale','next_session_target_scale'):
            if float(row[field])!=float(signal[field]):
                raise ValueError('Saved CSV and result scales conflict')


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--config',type=Path,required=True)
    p.add_argument('--url',required=True);p.add_argument('--sha256',required=True)
    p.add_argument('--strategy-sha',required=True);p.add_argument('--date',required=True)
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    approved=json.loads(args.config.read_bytes())
    exact=approved.get('reports',{}).get(args.date)
    if (approved.get('schema')!='member_daily_reviewed_reports_v1' or exact!={'url':args.url,'sha256':args.sha256,'strategy_sha':args.strategy_sha}
            or not args.url.startswith('https://github.com/liuruojiang/microcap/releases/download/')):
        raise ValueError('Report recovery is not the exact reviewed date/source/archive')
    opener=urllib.request.build_opener(StripCrossOriginAuthRedirectHandler())
    with opener.open(args.url,timeout=60) as response:raw=response.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES or hashlib.sha256(raw).hexdigest()!=args.sha256:
        raise ValueError('Reviewed report archive SHA/size mismatch')
    unpack(raw,args.out)
    verify(args.out,args.strategy_sha,args.date)


if __name__=='__main__':main()
