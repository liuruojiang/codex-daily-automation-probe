"""Verify a separately approved test-source role without changing runtime identity.

Only Git object reads are performed by verify, unless --fetch-missing is explicit.
Materialize creates a fresh independent test tree from the full approved Git archive.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import re
import subprocess
import sys
import tarfile
import tempfile

ORIGINS={"https://github.com/liuruojiang/microcap","https://github.com/liuruojiang/microcap.git",
         "git@github.com:liuruojiang/microcap","git@github.com:liuruojiang/microcap.git",
         "ssh://git@github.com/liuruojiang/microcap","ssh://git@github.com/liuruojiang/microcap.git"}
SCHEMA="microcap_daily_test_source_verification_v1"


def require(value,message):
    if not value:raise ValueError("Microcap test source: "+message)


def sha(raw):return hashlib.sha256(raw).hexdigest()


def canonical(value):return sha(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode())


def git(root,*args):
    return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.PIPE,timeout=120)


def safe_path(value):
    require(isinstance(value,str) and value and '\\' not in value and ':' not in value,'invalid Git path')
    path=PurePosixPath(value)
    require(not path.is_absolute() and value==path.as_posix() and not any(p in ('..','.git') for p in path.parts),'unsafe Git path')
    return path


def tree(root,commit):
    result={}
    for record in git(root,'ls-tree','-rz','--full-tree',commit).split(b'\0'):
        if not record:continue
        header,path=record.split(b'\t',1);mode,kind,oid=header.decode('ascii').split()
        name=path.decode('utf8');safe_path(name)
        require(kind=='blob' and mode in ('100644','100755','120000'),'unsupported tracked source entry: '+name)
        result[name]={'mode':mode,'oid':oid}
    require(result,'empty Git source tree')
    return result


def verify(runtime_root:Path,config_path:Path,*,fetch_missing:bool=False)->dict:
    require(not sys.flags.optimize,'optimized Python is forbidden')
    root=Path(runtime_root).resolve();config_path=Path(config_path).resolve();raw=config_path.read_bytes()
    config=json.loads(raw);role=config.get('daily_test_source')
    require(isinstance(role,dict),'daily_test_source declaration missing')
    runtime=role.get('runtime_sha');source=role.get('source_sha')
    require(all(isinstance(v,str) and re.fullmatch('[0-9a-f]{40}',v) for v in (runtime,source)),'immutable runtime/test commits required')
    require(config.get('strategy_sha')==runtime,'test runtime differs from approved production pin')
    origin=git(root,'remote','get-url','origin').decode().strip()
    require(origin in ORIGINS,'origin is not the official microcap repository')
    head=git(root,'rev-parse','HEAD').decode().strip()
    require(head in (runtime,source),'actual repository HEAD is outside the two declared roles')
    for commit in dict.fromkeys((runtime,source)):
        try:git(root,'cat-file','-e',commit+'^{commit}')
        except subprocess.CalledProcessError:
            require(fetch_missing,'declared immutable Git commit is missing')
            git(root,'fetch','--no-tags','origin',commit)
        require(git(root,'rev-parse',commit+'^{commit}').decode().strip()==commit,'Git ref is not the exact commit')
    subprocess.run(['git','-C',str(root),'merge-base','--is-ancestor',runtime,source],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120)
    old,new=tree(root,runtime),tree(root,source)
    allowed=role.get('allowed_changes')
    require(isinstance(allowed,list),'precise allowed_changes required')
    entries={}
    for item in allowed:
        require(isinstance(item,dict),'invalid allowed change')
        name=item.get('path');safe_path(name)
        require(name.startswith(('tests/','docs/')),'only reviewed test/doc changes are allowed')
        require(name not in entries,'duplicate allowed change')
        require('old_git_blob_sha256' in item and 'new_git_blob_sha256' in item,'explicit old/new Git hashes required')
        entries[name]=item
    changed={p for p in old.keys()|new.keys() if old.get(p)!=new.get(p)}
    require(changed==set(entries),'actual Git difference is not the exact approved test/doc set')
    changes=[]
    for name in sorted(changed):
        before,after=old.get(name),new.get(name)
        require(after is not None,'test/doc deletion cannot reduce the mandatory source closure')
        require(before is None or before['mode']==after['mode'],'unexpected source mode change')
        old_hash=sha(git(root,'show',runtime+':'+name)) if before else None
        new_hash=sha(git(root,'show',source+':'+name))
        require(entries[name].get('old_git_blob_sha256')==old_hash
                and entries[name].get('new_git_blob_sha256')==new_hash,'approved old/new Git bytes differ: '+name)
        changes.append({'path':name,'status':'modified' if before else 'added',
                        'old_git_blob_sha256':old_hash,'new_git_blob_sha256':new_hash,
                        'old_git_blob_oid':before['oid'] if before else None,'new_git_blob_oid':after['oid']})
    runtime_tree={p:v for p,v in old.items() if not p.startswith(('tests/','docs/'))}
    require(runtime_tree=={p:v for p,v in new.items() if not p.startswith(('tests/','docs/'))},'runtime tree content changed')
    require(config_path.read_bytes()==raw and git(root,'rev-parse','HEAD').decode().strip()==head,'config or repository HEAD changed during verification')
    require(git(root,'remote','get-url','origin').decode().strip()==origin,'repository origin changed during verification')
    return {'schema':SCHEMA,'ok':True,'verified':True,'runtime_equivalent':True,'verified_at':datetime.now(timezone.utc).isoformat(),
            'repository_root':str(root),'runtime_sha':runtime,'test_source_sha':source,'actual_repo_head':head,
            'runtime_head_before':head,'runtime_head_after':head,'origin':'https://github.com/liuruojiang/microcap.git',
            'verifier_sha256':sha(Path(__file__).read_bytes()),'config_sha256':sha(raw),'changes':changes,
            'runtime_equivalence':{'all_unapproved_tree_entries_equal':True,'all_non_test_doc_tree_entries_equal':True,
                                   'runtime_path_count':len(runtime_tree),'runtime_tree_bindings_sha256':canonical(runtime_tree)},
            'test_tree_bindings_sha256':canonical(new),'git_objects_verified':True,
            'runtime_or_package_author_relabelled':False,'network_fetch_explicit':fetch_missing}


def materialize(runtime_root,config_path,test_root,*,fetch_missing=False):
    proof=verify(runtime_root,config_path,fetch_missing=fetch_missing)
    require(not Path(test_root).exists() and not Path(test_root).is_symlink(),'test target already exists')
    root=Path(runtime_root).resolve();target=Path(test_root).resolve()
    require(not target.exists() and not target.is_relative_to(root) and not root.is_relative_to(target),'fresh disjoint test tree required')
    target.parent.mkdir(parents=True,exist_ok=True)
    source=proof['test_source_sha'];full_inventory=tree(root,source)
    # Same complete executable/test dependency scope as the formal local preparation.
    # Historical research directories contain tracked pytest symlinks, not test inputs.
    authority='outputs/microcap_top100_mom16_biweekly_live_v2_0_base_frozen_tail_authority.json'
    selected=[name for name in full_inventory if '/' not in name or name.split('/')[0] in
              {'data','docs','scripts','tests','poe_bots','.github','config'} or name==authority]
    inventory={name:full_inventory[name] for name in selected}
    require(all(entry['mode']!='120000' for entry in inventory.values()),'linked executable/test archive entry')
    bindings={};case_names=set()
    git(root,'worktree','add','--detach','--no-checkout',str(target),source)
    with tempfile.TemporaryDirectory(prefix='microcap-test-archive-',dir=target.parent) as temporary:
        archive_path=Path(temporary)/'source.tar'
        selections=sorted({name.split('/')[0] for name in selected if not name.startswith('outputs/')} | ({authority} if authority in selected else set()))
        git(root,'-c','core.autocrlf=false','-c','core.eol=lf','archive',
            '--format=tar','--output='+str(archive_path),source,'--',*selections)
        with tarfile.open(archive_path,'r:') as archive:
            for member in archive:
                name=member.name.rstrip('/');safe_path(name);destination=target/name
                if member.isdir():destination.mkdir(parents=True,exist_ok=True);continue
                require(member.isfile() and name in inventory,'unapproved archive entry: '+name)
                folded=name.casefold();require(folded not in case_names,'duplicate/case-colliding archive path');case_names.add(folded)
                require(not destination.exists(),'existing archive destination')
                destination.parent.mkdir(parents=True,exist_ok=True);digest=hashlib.sha256();blob=hashlib.sha1(('blob '+str(member.size)+'\0').encode())
                stream=archive.extractfile(member)
                with destination.open('xb') as output:
                    while chunk:=stream.read(1024*1024):output.write(chunk);digest.update(chunk);blob.update(chunk)
                require(blob.hexdigest()==inventory[name]['oid'],'archive bytes differ from approved Git object: '+name)
                bindings[name]=digest.hexdigest()
    require(set(bindings)==set(inventory),'incomplete test-source archive')
    git(target,'read-tree',source)
    require(git(target,'rev-parse','HEAD').decode().strip()==source,'independent test tree HEAD differs')
    after=verify(root,config_path)
    require(after['runtime_sha']==proof['runtime_sha'] and after['test_source_sha']==source
            and after['config_sha256']==proof['config_sha256'] and after['actual_repo_head']==proof['actual_repo_head'],'source roles changed during materialization')
    return {**proof,'materialized_test_root':str(target),'materialized_complete_test_source':True,
            'materialized_scope':'all_root_files_and_data_docs_scripts_tests_poe_bots_github_config_plus_formal_authority',
            'materialized_source_files':bindings,'materialized_source_files_sha256':canonical(bindings),
            'runtime_head_unchanged':True,'package_or_runtime_state_modified':False}


def check_materialized(receipt_path):
    proof=json.loads(Path(receipt_path).read_bytes());require(proof.get('ok') is True and proof.get('verified') is True,'verified test receipt missing')
    require(proof.get('verifier_sha256')==sha(Path(__file__).read_bytes()),'test verifier bytes changed')
    target=Path(proof['materialized_test_root']).resolve();bindings=proof['materialized_source_files']
    require(canonical(bindings)==proof['materialized_source_files_sha256'],'test bindings changed')
    require(git(target,'rev-parse','HEAD').decode().strip()==proof['test_source_sha'],'test-source HEAD changed')
    for name,digest in bindings.items():
        safe_path(name);path=target/name;require(not path.is_symlink() and sha(path.read_bytes())==digest,'test source bytes changed: '+name)
    for directory,dirs,files in os.walk(target,followlinks=False):
        for name in dirs:require(not (Path(directory)/name).is_symlink(),'test tree has a symlink directory')
        for name in files:
            path=Path(directory)/name
            if path.suffix in ('.py','.pyw'):
                require(path.relative_to(target).as_posix() in bindings,'unapproved additional executable test source')
    return {'schema':SCHEMA,'ok':True,'verified':True,'runtime_sha':proof['runtime_sha'],'test_source_sha':proof['test_source_sha'],
            'materialized_source_files_sha256':canonical(bindings),'source_before_equals_after':True,
            'verified_at':datetime.now(timezone.utc).isoformat()}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ('verify','materialize'):
        p=sub.add_parser(name);p.add_argument('--runtime-root',type=Path,required=True);p.add_argument('--config',type=Path,required=True)
        p.add_argument('--out',type=Path,required=True);p.add_argument('--fetch-missing',action='store_true')
        if name=='materialize':p.add_argument('--test-root',type=Path,required=True)
    p=sub.add_parser('check-materialized');p.add_argument('--receipt',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();require(not args.out.exists(),'existing verification receipt is never overwritten')
    if args.command=='verify':report=verify(args.runtime_root,args.config,fetch_missing=args.fetch_missing)
    elif args.command=='materialize':report=materialize(args.runtime_root,args.config,args.test_root,fetch_missing=args.fetch_missing)
    else:report=check_materialized(args.receipt)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    with args.out.open('x',encoding='utf8') as output:json.dump(report,output,indent=2,ensure_ascii=False);output.write('\n')
    print(json.dumps({k:v for k,v in report.items() if k!='materialized_source_files'},ensure_ascii=False))


if __name__=='__main__':main()
