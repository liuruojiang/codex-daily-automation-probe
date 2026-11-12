"""Prepare and inspect one exact M8 v4 archive candidate; never publish it.

The original M3 archive remains the data authority.  Code-only preflight must
pass before its restore, and the native archive recertifier must subsequently
earn its own certificate.  This helper does not refresh market data, send mail,
change active runtime pins, or create a normal production recovery artifact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


SCHEMA = 'microcap_multisource_archive_candidate_v1'
REVISION = 'exact_m3_20261110_typed_dualquote_fallback_source_only_v4'
ORIGINAL = '872abe41d48f818363d879cdf98b297d230377b4'
READER_PARENT = 'a7c1b9ac33454be4367880a0989dfc1466640503'
PRODUCER_PARENT = '3fe2e15ee9e3f2ee7e25bbc328479f1fd9910d36'
DATE = '2026-11-10'
BUNDLE_NAME = 'microcap-whole-delivery-state-archive-20261110-m8-typed-dualquote-v4.zip'
ORIGINAL_ARCHIVE = {
    'url': 'https://github.com/liuruojiang/codex-daily-automation-probe/releases/download/microcap-scoped-whole-20261110-872abe41/microcap-whole-delivery-state-close-20261110.zip',
    'sha256': '3d9aa183b614b272597bf81cfbd3b6fefdde021347fd36586e8047a6dc025e42',
    'bytes': 673074646,
    'state_manifest_sha256': 'b65cb02a8e14c871ae7b404def673b72ead0bf792654ab573c93df6cd2a4f4d5',
    'data_inventory_sha256': '42899f51e86c92ac4438bf25c45bd9419df3b9ba0c6997fa4fabe1c0af61d860',
}
SCOPE = {'archive_only': True, 'market_refresh': False, 'current_signal_ready': False,
         'production_activation': False, 'publication': False, 'smtp': False}


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)+'\n').encode()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def exact(value, length):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{'+str(length)+'}', value) is not None


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args])


def checked_source_path(name):
    if (not isinstance(name, str) or not name or any(c in name for c in '\\:\r\n\0')
            or PurePosixPath(name).is_absolute() or PurePosixPath(name).as_posix()!=name
            or any(p.casefold() in ('', '.', '..', '.git') or p.endswith(('.', ' ')) for p in name.split('/'))
            or name.casefold().startswith(('outputs/', '.microcap_index_cache/'))
            or name.casefold() in ('outputs', '.microcap_index_cache')):
        raise ValueError('Candidate review must contain regular source paths only')
    return name


def clean_head(root, expected=None):
    root=Path(root).resolve()
    actual=git(root,'rev-parse','HEAD').decode().strip()
    if not exact(actual,40) or (expected is not None and actual!=expected):
        raise ValueError('Candidate checkout does not match its exact commit')
    if git(root,'status','--porcelain=v1','-z','--untracked-files=all'):
        raise ValueError('Candidate source checkout must be clean before freezing/preflight')
    return actual


def complete_review(root, before, after):
    """Read the complete Git diff, including tests/docs, without checking out."""
    if not all(exact(v,40) for v in (before,after)) or before==after:
        raise ValueError('Candidate review needs distinct exact Git commits')
    names=[n.decode('utf-8') for n in git(root,'diff','--no-renames','--name-only','-z',before,after).split(b'\0') if n]
    if not names or len({n.casefold() for n in names})!=len(names):
        raise ValueError('Candidate review has an empty or aliased changed path set')
    changes={}
    for name in sorted(names):
        checked_source_path(name); pair=[]
        for revision in (before,after):
            entry=git(root,'ls-tree','-z',revision,'--',name)
            if not entry:
                pair.append(None); continue
            records=entry.rstrip(b'\0').split(b'\0')
            if len(records)!=1:
                raise ValueError('Candidate review path is not a single Git object')
            meta,actual=records[0].split(b'\t',1);mode,kind,oid=meta.split()
            if actual.decode('utf-8')!=name or mode not in (b'100644',b'100755') or kind!=b'blob':
                raise ValueError('Candidate review permits regular Git source blobs only')
            pair.append(sha(git(root,'cat-file','blob',oid.decode())))
        if pair[1] is None or pair[0]==pair[1]:
            raise ValueError('Candidate review cannot remove source paths or contain unchanged pairs')
        changes[name]=pair
    return {'schema_version':1,'baseline_sha':before,'runtime_sha':after,'changes':changes}


# These are actual source-code probes.  They never instantiate a source data
# package or claim a historical/native gate has run without the full archive.
PROBE = r'''
import base64,gzip,hashlib,json,sys,zipfile
from pathlib import Path
root=Path(sys.argv[1]).resolve();sys.path.insert(0,str(root))
options=json.load(sys.stdin)
from scripts.stock_input_contract import source_fingerprint
if options['operation']=='producer':
    result={'sources':source_fingerprint(root)}
else:
    from scripts import scoped_archive_source_revalidation as a,top100_delivery as d
    if options['operation']=='reader':
        from scripts import scoped_source_consumer as consumer
        packet=json.loads(gzip.decompress((root/'tests/fixtures/archive_source_revalidation_m3_20261110.json.gz').read_bytes()))
        assert packet['metadata']==a.BASELINE
        oldraw=base64.b64decode(packet['objects']['bundle-manifest.json']['base64'],validate=True)
        original=a.original_manifest(oldraw)
        names=[f'outputs/{n}' for n in (*d.BASE_FILES.values(),d.BASE_PANEL)]+[d.AUTHORITY]
        names += [f'microcap_top100_mom16_biweekly_live_v2_{v}.py' for v in d.COSTED]
        names += ['scripts/top100_data_contracts.py']+d.evidence_source_files(root)+d.RETURN_BASIS_FILES
        changes=a.input_changes();added={n for n,pair in changes.items() if pair[0] is None}
        assert set(names)==set(original['inputs'])|{a.MODULE}|added
        def native_hash(name):
            return hashlib.sha256((root/name).read_bytes()).hexdigest() if name.startswith('data/st_notice_evidence/') else d.sha(root/name)
        observed={n:(original['inputs'].get(n),native_hash(n)) for n in names
                  if n!=a.MODULE and not n.startswith('outputs/') and original['inputs'].get(n)!=native_hash(n)}
        assert observed==changes
        a.checked_source(root)
        current=source_fingerprint(root)
        # Explicit code-contract probe: these are measured Git source hashes,
        # not a fabricated earned source-data manifest or successful collection.
        probe={'runtime_sha':options['producer_sha'],'producer_context':
               {'runtime_sha':options['producer_sha'],'native_sources':options['producer_sources']}}
        assert consumer.validate_source_contract(root,probe)==current
        assert options['producer_sources']==current
        result={'native_revision':a.REVISION,'module_sha256':d.sha(root/a.MODULE),
                'input_changes_sha256':hashlib.sha256(json.dumps(changes,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
                'input_changes_count':len(changes),'producer_sources':options['producer_sources'],
                'reader_sources':current,'baseline_sha256':hashlib.sha256(json.dumps(a.BASELINE,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
                'original_archive':{'sha256':a.BASELINE['source_bundle_sha256'],
                                    'state_manifest_sha256':a.BASELINE['state_manifest_sha256'],
                                    'data_inventory_sha256':a.BASELINE['state_inventory_sha256']}}
    else:
        bundle=Path(options['bundle']);pack=options['pack']
        a.checked_source(root)
        assert d.sha(root/a.MODULE)==options['native_contract']['module_sha256']
        assert hashlib.sha256(json.dumps(a.input_changes(),sort_keys=True,separators=(',',':')).encode()).hexdigest()==options['native_contract']['input_changes_sha256']
        assert pack.get('ok') is True and pack.get('expected_date')==a.DATE
        assert pack.get('release_sha')==options['runtime_sha'] and pack.get('scope')=='whole_workspace_delivery'
        assert pack.get('archive_session')==a.DATE and pack.get('current_signal_ready') is False and pack.get('signal_ready') is False
        with zipfile.ZipFile(bundle) as z:
            names=z.namelist();assert len(names)==len(set(names))
            manifest=json.loads(z.read(d.MANIFEST));original=a.checked_document(manifest)
            assert manifest['release_sha']==options['runtime_sha'] and manifest[a.ACTIVE]['revision']==a.REVISION
            assert manifest.get('current_signal_ready') is False and manifest.get('signal_ready') is False
            a.require_transition(original,manifest)
            assert manifest[a.ACTIVE]['validator_inputs'][a.MODULE]==options['native_contract']['module_sha256']
            inventory=json.loads(z.read('top100_realtime_state_manifest.json'))['files']
            a.inventory_matches(inventory)
            assert set(names)=={entry['path'] for entry in inventory}|{'top100_realtime_state_manifest.json'}
            for name,wanted in a.BASELINE['economic_csvs'].items():
                assert hashlib.sha256(z.read(name)).hexdigest()==wanted
            assert hashlib.sha256(z.read(a.PROXY)).hexdigest()==a.BASELINE['proxy_sha256']
            result={'revision':a.REVISION,'archive_session':a.DATE,'economic_csv_count':len(a.BASELINE['economic_csvs']),
                    'unchanged_data_objects':len(inventory)-1,'data_inventory_sha256':a.BASELINE['state_inventory_sha256'],
                    'certificate_sha256':hashlib.sha256(json.dumps(manifest[a.ACTIVE],sort_keys=True,separators=(',',':')).encode()).hexdigest()}
print(json.dumps(result,sort_keys=True))
'''


def probe(root, **options):
    result=subprocess.run([sys.executable,'-B','-c',PROBE,str(Path(root).resolve())],input=canonical(options),
                          stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
    if result.returncode:
        raise ValueError('Native candidate code/archive probe failed: '+result.stderr.decode('utf-8',errors='replace')[-3000:])
    return json.loads(result.stdout)


def code_contract(reader, producer, producer_sha):
    sources=probe(producer,operation='producer')['sources']
    value=probe(reader,operation='reader',producer_sha=producer_sha,producer_sources=sources)
    if (value.get('native_revision')!=REVISION or value.get('input_changes_count')!=201
            or len(value.get('producer_sources',{}))!=18 or value.get('producer_sources')!=value.get('reader_sources')
            or any(value.get('original_archive',{}).get(k)!=ORIGINAL_ARCHIVE[k]
                   for k in ('sha256','state_manifest_sha256','data_inventory_sha256'))):
        raise ValueError('M8 native source contract differs from the exact original M3 review')
    return value


def pending_config():
    return {'schema':SCHEMA,'status':'pending_native_freeze','archive_session':DATE,'native_revision':REVISION,
            'producer_runtime_sha':None,'activation_review':None,'native_contract':None,
            'original_archive':dict(ORIGINAL_ARCHIVE),'candidate_bundle_name':BUNDLE_NAME,'scope':dict(SCOPE)}


def load_config(path):
    value=json.loads(Path(path).read_bytes())
    expected=set(pending_config())
    if not isinstance(value,dict) or set(value)!=expected:
        raise ValueError('Invalid v4 archive candidate config schema')
    if value['status']!='frozen_for_archive_recertification':
        raise ValueError('Native M8 candidate is not frozen; do not restore or activate')
    if any(value[k]!=pending_config()[k] for k in ('schema','archive_session','native_revision','original_archive','candidate_bundle_name','scope')):
        raise ValueError('Archive candidate changed fixed original-data or no-send scope')
    if any(type(value['scope'][key]) is not bool for key in SCOPE):
        raise ValueError('Archive candidate scope requires explicit boolean values')
    review=value['activation_review'];contract=value['native_contract']
    if (not exact(value['producer_runtime_sha'],40) or value['producer_runtime_sha']==PRODUCER_PARENT
            or not isinstance(review,dict) or set(review)!={'schema_version','baseline_sha','runtime_sha','changes'}
            or review['schema_version']!=1 or type(review['schema_version']) is not int or review['baseline_sha']!=ORIGINAL
            or not exact(review['runtime_sha'],40) or review['runtime_sha'] in (ORIGINAL,READER_PARENT)
            or not isinstance(review['changes'],dict) or not review['changes']
            or len({n.casefold() for n in review['changes']})!=len(review['changes'])
            or not isinstance(contract,dict) or contract.get('native_revision')!=REVISION):
        raise ValueError('Archive candidate requires exact frozen new P3/M8 identities')
    contract_keys={'native_revision','module_sha256','input_changes_sha256','input_changes_count',
                   'producer_sources','reader_sources','baseline_sha256','original_archive'}
    if (set(contract)!=contract_keys or contract['input_changes_count']!=201
            or type(contract['input_changes_count']) is not int
            or any(not exact(contract[key],64) for key in ('module_sha256','input_changes_sha256','baseline_sha256'))
            or contract['producer_sources']!=contract['reader_sources']
            or not isinstance(contract['producer_sources'],dict) or len(contract['producer_sources'])!=18
            or contract['original_archive']!={k:ORIGINAL_ARCHIVE[k] for k in ('sha256','state_manifest_sha256','data_inventory_sha256')}):
        raise ValueError('Archive candidate has an incomplete native code contract')
    for name,digest in contract['producer_sources'].items():
        checked_source_path(name)
        if not exact(digest,64):raise ValueError('Invalid measured producer source hash')
    for name,pair in review['changes'].items():
        checked_source_path(name)
        if (not isinstance(pair,list) or len(pair)!=2 or pair[0]==pair[1] or not exact(pair[1],64)
                or (pair[0] is not None and not exact(pair[0],64))):
            raise ValueError('Archive candidate review needs exact old/new raw Git hashes')
    return value


def write_new_or_same(path,value,*,allow_pending=False):
    path=Path(path).resolve();raw=encoded(value)
    if path.exists():
        existing=path.read_bytes()
        if json.loads(existing)==value:return
        if not allow_pending or json.loads(existing)!=pending_config():
            raise ValueError('Preserve existing candidate evidence/config; use a new output path')
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.tmp')
    with temporary.open('xb') as stream:stream.write(raw)
    os.replace(temporary,path)


def freeze(reader,producer,path):
    runtime=clean_head(reader);producer_sha=clean_head(producer)
    subprocess.run(['git','-C',str(reader),'merge-base','--is-ancestor',READER_PARENT,runtime],check=True)
    subprocess.run(['git','-C',str(producer),'merge-base','--is-ancestor',PRODUCER_PARENT,producer_sha],check=True)
    value={**pending_config(),'status':'frozen_for_archive_recertification','producer_runtime_sha':producer_sha,
           'activation_review':complete_review(reader,ORIGINAL,runtime),
           'native_contract':code_contract(reader,producer,producer_sha)}
    write_new_or_same(path,value,allow_pending=True)
    load_config(path)
    return {'status':'frozen_code_contract_only','runtime_sha':runtime,'producer_runtime_sha':producer_sha,
            'config_sha256':sha(Path(path).read_bytes()),'reviewed_git_paths':len(value['activation_review']['changes']),
            'archive_recertification_earned':False,'publication':False}


def preflight(config,reader,producer,review_out):
    value=load_config(config);review=value['activation_review']
    clean_head(reader,review['runtime_sha']);clean_head(producer,value['producer_runtime_sha'])
    subprocess.run(['git','-C',str(reader),'merge-base','--is-ancestor',READER_PARENT,review['runtime_sha']],check=True)
    subprocess.run(['git','-C',str(producer),'merge-base','--is-ancestor',PRODUCER_PARENT,value['producer_runtime_sha']],check=True)
    if complete_review(reader,ORIGINAL,review['runtime_sha'])!=review:
        raise ValueError('M8 complete Git diff differs from its frozen review')
    if code_contract(reader,producer,value['producer_runtime_sha'])!=value['native_contract']:
        raise ValueError('P3/M8 native code contract differs from frozen source hashes')
    write_new_or_same(review_out,review)
    return {'status':'code_preflight_verified_not_data_recertified','runtime_sha':review['runtime_sha'],
            'producer_runtime_sha':value['producer_runtime_sha'],'config_sha256':sha(Path(config).read_bytes()),
            'reviewed_git_paths':len(review['changes']),'native_input_changes':value['native_contract']['input_changes_count'],
            'archive_recertification_earned':False,'publication':False}


def verify_candidate(config,root,bundle,pack_report):
    value=load_config(config);bundle=Path(bundle).resolve();root=Path(root).resolve()
    if bundle.name!=BUNDLE_NAME or bundle.is_relative_to(root):
        raise ValueError('Candidate archive requires its unique external v4 destination')
    runtime=value['activation_review']['runtime_sha']
    if git(root,'rev-parse','HEAD').decode().strip()!=runtime:
        raise ValueError('Packed candidate runtime differs from frozen M8')
    result=probe(root,operation='bundle',bundle=str(bundle),pack=json.loads(Path(pack_report).read_bytes()),
                 runtime_sha=runtime,native_contract=value['native_contract'])
    with bundle.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    return {**result,'status':'native_earned_v4_archive_candidate','bundle':str(bundle),'bundle_sha256':digest,
            'bundle_bytes':bundle.stat().st_size,'runtime_sha':runtime,'producer_runtime_sha':value['producer_runtime_sha'],
            'config_sha256':sha(Path(config).read_bytes()),'scope':SCOPE,'production_activated':False,'published':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='action',required=True)
    for name in ('freeze','preflight'):
        p=sub.add_parser(name);p.add_argument('--reader-root',type=Path,required=True);p.add_argument('--producer-root',type=Path,required=True)
        p.add_argument('--config',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
        if name=='preflight':p.add_argument('--review-out',type=Path,required=True)
    p=sub.add_parser('settings');p.add_argument('--config',type=Path,required=True);p.add_argument('--github-output',type=Path)
    p=sub.add_parser('verify-candidate')
    for n in ('config','root','bundle','pack-report','out'):p.add_argument('--'+n,type=Path,required=True)
    args=parser.parse_args()
    if args.action=='settings':
        value=load_config(args.config)
        result={'runtime_sha':value['activation_review']['runtime_sha'],'producer_sha':value['producer_runtime_sha'],
                'baseline_sha':ORIGINAL,'archive_session':DATE,'bundle_name':BUNDLE_NAME}
        if args.github_output:
            with args.github_output.open('a',encoding='utf-8') as stream:
                for key,item in result.items():stream.write(key+'='+item+'\n')
    elif args.action=='freeze':result=freeze(args.reader_root,args.producer_root,args.config)
    elif args.action=='preflight':result=preflight(args.config,args.reader_root,args.producer_root,args.review_out)
    else:result=verify_candidate(args.config,args.root,args.bundle,args.pack_report)
    if hasattr(args,'out'):write_new_or_same(args.out,result)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
