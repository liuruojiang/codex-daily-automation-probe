"""Optional source prewarm for rebalance; never substitute for native delivery."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request

from prepare_microcap_local import atomic,encode,sha
from microcap_scoped_inputs import MAX_BYTES,RUNTIME,SOURCE_RUNTIMES,install,read_bundle


def load_config(path):
    value=json.loads(Path(path).read_bytes())
    if (set(value)!={'schema','owner','scoped_inputs'} or value['schema']!='microcap_local_maintenance_host_v1'
            or value['owner'] not in ('local','cloud')):raise ValueError('Invalid maintenance host config')
    item=value['scoped_inputs']
    if item is not None:
        if set(item)!={'url','sha256','target','runtime_sha','producer_commit'}:raise ValueError('Invalid scoped source pointer')
        parsed=urllib.parse.urlsplit(item['url'])
        if (parsed.scheme!='https' or parsed.netloc!='github.com' or parsed.query or parsed.fragment
                or not parsed.path.startswith('/liuruojiang/codex-daily-automation-probe/releases/download/microcap-local-sources-')
                or not parsed.path.endswith('.zip') or '..' in parsed.path or '%' in parsed.path):
            raise ValueError('Scoped source URL is outside the approved repository release route')
        if (re.fullmatch('[0-9a-f]{64}',item['sha256']) is None or item['runtime_sha'] not in SOURCE_RUNTIMES
                or re.fullmatch('[0-9a-f]{40}',item['producer_commit']) is None):
            raise ValueError('Scoped source pointer identity differs')
        date.fromisoformat(item['target'])
    return value


def restore(root,config_path,not_after,out,opener=None):
    config=load_config(config_path);item=config['scoped_inputs'];out=Path(out)
    if item is None:return dict(status='no_local_source_bundle_configured',final_strategy_delivery=False)
    delta=(date.fromisoformat(not_after)-date.fromisoformat(item['target'])).days
    if not 0<=delta<=7:raise ValueError('Configured source bundle is from a future or stale weekly window')
    if opener is None:opener=urllib.request.urlopen
    with opener(item['url'],timeout=60) as response:raw=response.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES or sha(raw)!=item['sha256']:raise ValueError('Downloaded scoped source bundle SHA differs')
    out.mkdir(parents=True,exist_ok=False)
    bundle=out/'scoped-source-inputs.zip';atomic(bundle,raw)
    manifest,_,_=read_bundle(bundle,not_after,item['sha256'])
    if manifest['target']!=item['target'] or manifest.get('producer_commit')!=item['producer_commit']:
        raise ValueError('Scoped source release metadata differs from manifest')
    result=install(root,bundle,item['sha256'],not_after)
    atomic(out/'receipt.json',encode(result))
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--config',type=Path,required=True)
    p.add_argument('--not-after-date',required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();print(json.dumps(restore(a.root,a.config,a.not_after_date,a.out)))


if __name__=='__main__':main()
