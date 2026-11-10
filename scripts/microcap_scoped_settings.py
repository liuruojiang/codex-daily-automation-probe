"""Exact source/runtime pointers for the independent no-send maintenance job."""
import argparse
from datetime import date
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request
from prepare_microcap_local import atomic,sha
from maintain_microcap_local import RUNTIME

RELEASE_PREFIX='/liuruojiang/codex-daily-automation-probe/releases/download/'


def asset(value,kind):
    if not isinstance(value,dict) or set(value)!={'url','sha256','date'}:raise ValueError('Invalid scoped '+kind+' pointer')
    parsed=urllib.parse.urlsplit(value['url'])
    if (parsed.scheme!='https' or parsed.netloc!='github.com' or parsed.query or parsed.fragment
            or not parsed.path.startswith(RELEASE_PREFIX) or '..' in parsed.path or '%' in parsed.path
            or not parsed.path.endswith('.zip') or re.fullmatch('[0-9a-f]{64}',value['sha256']) is None):
        raise ValueError('Scoped pointer is outside the exact release route')
    date.fromisoformat(value['date']);return value


def load(path,require_seed=False):
    value=json.loads(Path(path).read_bytes())
    if (set(value)!={'schema','enabled','runtime_sha','producer_sha','whole_seed','weekly_roster'}
            or value['schema']!='microcap_scoped_rebalance_runtime_v1' or type(value['enabled']) is not bool
            or any(not isinstance(value[n],str) or re.fullmatch('[0-9a-f]{40}',value[n]) is None for n in ('runtime_sha','producer_sha'))
            or value['producer_sha']!=RUNTIME):raise ValueError('Scoped maintenance runtime configuration differs')
    asset(value['weekly_roster'],'weekly roster')
    if value['whole_seed'] is not None:asset(value['whole_seed'],'whole-state seed')
    elif require_seed or value['enabled']:raise ValueError('Scoped full-state recovery seed has not been accepted')
    return value


def download_roster(config,bundle):
    item=load(config,require_seed=True)['weekly_roster'];bundle=Path(bundle)
    if bundle.exists():raise ValueError('Preserve existing roster download evidence')
    with urllib.request.urlopen(item['url'],timeout=60) as response:raw=response.read(10*1024*1024+1)
    if len(raw)>10*1024*1024 or sha(raw)!=item['sha256']:raise ValueError('Downloaded weekly roster SHA differs')
    atomic(bundle,raw);return item


def download_sources(config,bundle,not_after):
    from restore_microcap_scoped_inputs import load_config
    from microcap_scoped_inputs import MAX_BYTES,read_bundle
    item=load_config(config)['scoped_inputs']
    if item is None:return dict(status='no_local_source_bundle_configured')
    delta=(date.fromisoformat(not_after)-date.fromisoformat(item['target'])).days
    if delta<0:raise ValueError('Local source pointer is from a future close')
    if delta>7:return dict(status='skipped_stale_optional_local_sources',target=item['target'])
    bundle=Path(bundle)
    if bundle.exists():
        if bundle.stat().st_size>MAX_BYTES:raise ValueError('Downloaded local source exceeds size limit')
        raw=bundle.read_bytes()
    else:
        with urllib.request.urlopen(item['url'],timeout=60) as response:raw=response.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES or sha(raw)!=item['sha256']:raise ValueError('Downloaded local source SHA differs')
    if not bundle.exists():atomic(bundle,raw)
    manifest,_,_=read_bundle(bundle,not_after,item['sha256'])
    if (manifest['target']!=item['target'] or manifest.get('producer_commit')!=item['producer_commit']
            or manifest.get('runtime_sha')!=item['runtime_sha']):
        raise ValueError('Local source pointer and original producer receipt differ')
    return dict(status='verified_local_source_bundle_downloaded',target=item['target'],sha256=item['sha256'],bundle=str(bundle.resolve()))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True)
    p.add_argument('--task',choices=('rebalance','scoped_acceptance','scoped_readback'))
    p.add_argument('--roster-bundle',type=Path);a=p.parse_args()
    if a.roster_bundle:
        print(json.dumps(download_roster(a.config,a.roster_bundle)));return
    value=load(a.config,require_seed=a.task in ('scoped_acceptance','scoped_readback'))
    run=value['enabled'] or a.task in ('scoped_acceptance','scoped_readback')
    print('run='+str(run).lower())
    for name in ('runtime_sha','producer_sha'):print(name+'='+value[name])
    if value['whole_seed']:
        print('seed_url='+value['whole_seed']['url']);print('seed_sha256='+value['whole_seed']['sha256'])
    print('roster_sha256='+value['weekly_roster']['sha256'])

if __name__=='__main__':main()
