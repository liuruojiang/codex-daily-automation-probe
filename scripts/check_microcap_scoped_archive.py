"""Native archive verification for recovery acceptance; no current signal claim."""
import argparse
from datetime import date
import json
from pathlib import Path
import sys

def check(root,receipt,out):
    root=Path(root).resolve();source=json.loads(Path(receipt).read_bytes())
    if source.get('status')!='scoped_whole_restore_verified' or source.get('restored') is not True:
        raise ValueError('No completed native restoration receipt')
    day=source['seed_date'];date.fromisoformat(day)
    sys.path.insert(0,str(root))
    from scripts import top100_delivery as delivery,realtime_state_bundle as state
    manifest=json.loads((root/delivery.MANIFEST).read_bytes())
    delivery._verify_release_files(root,manifest['release_sha'])
    report=delivery.validate_manifest(root,delivery.inspect_outputs(root,day))
    archived=state.validate_archive_state(root,date.fromisoformat(day))
    if not report.get('ok') or not archived.get('ok'):
        raise ValueError('Native complete archive validation failed: '+str(report.get('errors',[])+archived.get('errors',[])))
    value=dict(ok=True,archive_date=day,source_manifest_release_sha=source['source_manifest_release_sha'],
               restored_manifest_release_sha=manifest['release_sha'],reader_runtime=source['runtime_sha'],
               current_signal_ready=False,sending='skipped',orders_placed=False,
               streams=report['streams'],scope='whole_workspace_archive_recovery_acceptance')
    Path(out).write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8');return value

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('root','receipt','out'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();print(json.dumps(check(a.root,a.receipt,a.out)))
