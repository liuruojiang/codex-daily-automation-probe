"""Transport the one approved D10->D11 notice prefix without changing Native.

This sidecar is separate from stock inputs.  Its two original receipts retain
their paths, raw bytes and clocks; it grants neither D12 freshness nor delivery.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
import zipfile

from prepare_microcap_local import atomic, encode, writer_lock
from maintain_microcap_local import load_pool, validate_runtime, RUNTIME as ACTIVE_PRODUCER
from import_microcap_producer_inputs import checked_bootstrap
from microcap_scoped_whole_recovery import stream_download
from restore_microcap_rebalance_recovery import relative, sha_file

SCHEMA = 'microcap_notice_prefix_v1'
PROFILE = 'approved_d10_d11_notice_prefix_20261112_v1'
POINTER_SCHEMA = 'microcap_notice_prefix_pointer_v1'
PRODUCER = '3fe2e15ee9e3f2ee7e25bbc328479f1fd9910d36'
CONSUMER = 'a7c1b9ac33454be4367880a0989dfc1466640503'
ACTIVE_CONSUMER = '95d896fe58cc7cb84c0fcb7bd76534f62c9430f4'
PRODUCER_READERS = frozenset({PRODUCER, ACTIVE_PRODUCER})
TARGET = '2026-11-11'
PREFIX = '.microcap_index_cache/st_notice_sources/'
POINTER = '.microcap_index_cache/st_notice_archive_pointer.json'
FRESHNESS = PREFIX + 'freshness.json'
START = [PREFIX+'refresh_20261110T074136818665Z_b689e2946f53/archive.json',
         '62a74caa285b74caf72f0921ad46ca25126ae6eb0ee6e5425dddb5a6da8fad7f']
END = [PREFIX+'refresh_20261111T143208796316Z_2e7092817919/archive.json',
       'db23caa0f8ecc65e0720bb5f797023ec57313773dbc513284cc404608edbdc94']
RECEIPTS = [dict(path=PREFIX+'refresh_20261111T143208796316Z_2e7092817919/proof.json',
                 sha256='1afcedebbb8e0e2adbe1a529ffbb7b1d3728ec15fe91c0f8550c3daa1bfe672c'),
            dict(path=PREFIX+'refresh_20261112T010443196822Z_1489267b2428/proof.json',
                 sha256='651db4226dbdb200222be74017b7ca332adb838bd57bec213f18670a809a256b')]
END_POINTER = PREFIX+'refresh_20261112T010443196822Z_1489267b2428/previous_pointer.json'
MAX_FILES = 50000
MAX_BUNDLE = 2 * 1024**3
MAX_EXPANDED = 4 * 1024**3


class NoticePrefixError(RuntimeError):
    pass


def bound(root,name):
    relative(name)
    root=Path(root).absolute();path=root.joinpath(*PurePosixPath(name).parts)
    if (not path.resolve().is_relative_to(root.resolve())
            or any(p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction())
                   for p in (path,*path.parents) if p==root or p.is_relative_to(root))):
        raise NoticePrefixError('notice_prefix_object_contains_link_or_escapes_role')
    return path


def file_role(name):
    relative(name)
    return (name.startswith(PREFIX) and name.casefold() not in (FRESHNESS.casefold(),POINTER.casefold())
            and not name.casefold().endswith('.lock'))


def prefix_archive(old, new):
    """All old fields and every existing daily record must remain identical."""
    if not isinstance(old, dict) or not isinstance(new, dict):
        return False
    before, after = old.get('daily_increments'), new.get('daily_increments')
    return (isinstance(before,list) and bool(before) and isinstance(after,list)
            and len(after)>=len(before) and after[:len(before)]==before
            and {k:v for k,v in old.items() if k!='daily_increments'}==
                {k:v for k,v in new.items() if k!='daily_increments'})


# Execute P2 code in its own interpreter; importing another checkout's scripts
# in the controller process would mix module identities.  The child only reads.
NATIVE = r'''
import asyncio,sys,json,hashlib,socket,subprocess,urllib.request
from pathlib import Path
runtime,data,mode,profile=map(Path,sys.argv[1:5]);data=data.resolve();profile=profile.resolve()
spec=json.loads(sys.argv[5]);sys.path.insert(0,str(runtime.resolve()))
def no_io(*a,**k):raise RuntimeError('Notice prefix verification forbids HTTP and subprocess')
socket.socket.connect=socket.socket.connect_ex=urllib.request.urlopen=no_io
subprocess.Popen=no_io
from scripts.st_history_scope import source_closure,resolve_archive_reference,read_bound
from scripts.st_notice_refresh import freshness_source_files,archived_freshness_source_files
from scripts.scoped_source_consumer import verify_notice_chain
def read(root,ref):
 raw=(root/ref[0]).read_bytes()
 if hashlib.sha256(raw).hexdigest()!=ref[1]:raise ValueError('Notice authority raw bytes differ')
 return json.loads(raw)
def is_prefix(a,b):
 x=a.get('daily_increments');y=b.get('daily_increments')
 return (isinstance(x,list) and x and isinstance(y,list) and len(y)>=len(x) and y[:len(x)]==x
  and {k:v for k,v in a.items() if k!='daily_increments'}=={k:v for k,v in b.items() if k!='daily_increments'})
if str(mode)=='prefix':
 old=read(data,spec['start']);new=read(data,spec['end'])
 if not is_prefix(old,new) or len(new['daily_increments'])!=len(old['daily_increments'])+1:
  raise ValueError('Approved notice prefix changes prior archive fields')
 if new['daily_increments'][-1]['date']!=spec['target']:raise ValueError('Approved endpoint day differs')
 first=json.loads((data/spec['receipts'][0]['path']).read_bytes())
 last=json.loads((data/spec['receipts'][1]['path']).read_bytes())
 if (first['previous_authority']!=spec['start'] or first['authority']!=spec['end']
  or last['previous_authority']!=spec['end'] or last['authority']!=spec['end']):
  raise ValueError('Approved original receipt endpoints differ')
 chain=verify_notice_chain(data,spec['start'],spec['receipts'],spec['target'])
 files={};baseline={}
 for index,ref in enumerate((spec['start'],spec['end'])):
  for name,digest in source_closure(data,*ref).items():
   if name not in (spec['pointer'],spec['freshness']):
    if name in files and files[name]!=digest:raise ValueError('Conflicting archived notice object')
    files[name]=digest
    if index==0:baseline[name]=digest
 files.update(chain['source_files'])
 pointer=(data/spec['end_pointer']).read_bytes()
 if json.loads(pointer)!={'manifest_path':spec['end'][0],'manifest_sha256':spec['end'][1]}:
  raise ValueError('Original endpoint pointer receipt differs')
 files[spec['end_pointer']]=hashlib.sha256(pointer).hexdigest()
 result=dict(mode='prefix',native_runtime=spec['producer'],previous_authority=spec['start'],
  authority=chain['authority'],target=spec['target'],original_clocks_retained=True,
  old_prefix_unchanged=True,receipt_count=2,files=files,
  baseline_files=baseline,proof_files=chain['source_files'],
  sizes={name:(data/name).stat().st_size for name in files})
elif str(mode)=='forward':
 from datetime import date
 ref=list(resolve_archive_reference(data));current=read(data,ref)
 source_closure(data,*ref)
 day=current['daily_increments'][-1]['date'];requested=spec['forward_target']
 date.fromisoformat(day);date.fromisoformat(requested)
 active_path=data/spec['freshness']
 result=dict(mode='forward',native_runtime=spec['producer'],authority=ref,authority_day=day,
  requested_target=requested,old_prefix_unchanged=True,original_clocks_retained=True,
  state='noop',reason='no_forward_required',active_sha256=None)
 if active_path.exists():
  raw=active_path.read_bytes();active=json.loads(raw);digest=hashlib.sha256(raw).hexdigest()
  archived_freshness_source_files(data,spec['freshness'],digest,ref)
  result['active_sha256']=digest
  if requested>day:
   advancing=(data/ref[0]).parent/'proof.json'
   if active['previous_authority']!=ref:
    if not advancing.exists() or advancing.read_bytes()!=raw:
     raise ValueError('Active advancing checkpoint has no exact authority generation receipt')
    result['reason']='already_original_advancing_checkpoint'
    result['selected_spec']=dict(path=advancing.relative_to(data).as_posix(),sha256=digest)
   else:
    matches=[p for p in sorted((data/spec['prefix']).glob('refresh_*/proof.json')) if p.read_bytes()==raw]
    if not matches:raise ValueError('Active self-review has no original immutable receipt')
    body=advancing.read_bytes();old=json.loads(body)
    if (old['authority']!=ref or old['previous_authority']==ref or old['target']!=day
     or active['authority']!=ref or active['target']!=day):
     raise ValueError('Authority generation is not its original advancing checkpoint')
    selected=dict(path=advancing.relative_to(data).as_posix(),sha256=hashlib.sha256(body).hexdigest())
    active_spec=dict(path=matches[0].relative_to(data).as_posix(),sha256=digest)
    chain=verify_notice_chain(data,old['previous_authority'],[selected,active_spec],day)
    if chain['authority']!=ref:raise ValueError('Forward checkpoint chain ends at another authority')
    result.update(state='select',reason='original_advancing_checkpoint_for_next_native_forward',
     selected_spec=selected,active_spec=active_spec,selected_previous_authority=old['previous_authority'],
     source_files=chain['source_files'])
 elif requested>day:result['reason']='baseline_has_no_active_refresh_receipt'
else:
 ref=list(resolve_archive_reference(data));current=read(data,ref)
 source_closure(data,*ref)
 fresh=freshness_source_files(data)
 if ref==spec['start']:
  if current!=read(profile,spec['start']):raise ValueError('Private old authority differs from approved prefix')
  state='advance'
 else:
  endpoint=read(profile,spec['end'])
  if (not is_prefix(endpoint,current)
   or (ref!=spec['end'] and len(current['daily_increments'])<=len(endpoint['daily_increments']))):
   raise ValueError('Private authority is unrelated, unknown, or not a strict successor')
  state='covered' if fresh else 'covered_baseline_without_active_freshness'
 result=dict(mode='current',state=state,authority=ref,native_runtime=spec['producer'],
  old_prefix_unchanged=True,original_clocks_retained=True)
print(json.dumps(result))
'''


def runtime_role(root, expected):
    root=Path(root).resolve()
    actual=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],encoding='utf-8').strip()
    allowed=PRODUCER_READERS if expected==PRODUCER else {CONSUMER,ACTIVE_CONSUMER} if expected==CONSUMER else {expected}
    if actual not in allowed:
        raise NoticePrefixError('notice_prefix_native_git_role_differs')
    dirty=subprocess.check_output(['git','-C',str(root),'diff','--name-only','HEAD'],encoding='utf-8').splitlines()
    if any(not n.startswith(('.microcap_index_cache/','outputs/')) for n in dirty):
        raise NoticePrefixError('notice_prefix_native_executable_or_review_edits')
    return root


def real_native(runtime_root,data_root,mode,profile_root=None,requested_target=None):
    runtime_role(runtime_root,PRODUCER)
    reader=subprocess.check_output(['git','-C',str(runtime_root),'rev-parse','HEAD'],encoding='utf-8').strip()
    spec=dict(start=START,end=END,target=TARGET,receipts=RECEIPTS,end_pointer=END_POINTER,
              producer=reader,pointer=POINTER,freshness=FRESHNESS,prefix=PREFIX,
              forward_target=requested_target)
    process=subprocess.run([sys.executable,'-B','-X','utf8','-c',NATIVE,str(runtime_root),str(data_root),
        mode,str(profile_root or data_root),json.dumps(spec)],capture_output=True,encoding='utf-8',
        env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
    if process.returncode:
        raise NoticePrefixError('notice_prefix_original_native_verification_failed: '+process.stderr[-2000:])
    try:result=json.loads(process.stdout.strip().splitlines()[-1])
    except (IndexError,ValueError):raise NoticePrefixError('notice_prefix_native_response_invalid') from None
    if (result.get('native_runtime')!=reader or result.get('old_prefix_unchanged') is not True
            or result.get('original_clocks_retained') is not True):
        raise NoticePrefixError('notice_prefix_native_proof_incomplete')
    return result


def validate_manifest(value):
    if (not isinstance(value,dict) or value.get('schema')!=SCHEMA or value.get('profile')!=PROFILE
            or value.get('producer_runtime')!=PRODUCER or value.get('consumer_runtime')!=CONSUMER
            or value.get('target')!=TARGET or value.get('previous_authority')!=START or value.get('authority')!=END
            or value.get('receipts')!=RECEIPTS or value.get('active_freshness_spec')!=RECEIPTS[1]
            or not isinstance(value.get('end_pointer_spec'),dict)
            or set(value['end_pointer_spec'])!={'path','sha256'} or value['end_pointer_spec'].get('path')!=END_POINTER
            or value.get('source_inputs_only') is not True or value.get('old_prefix_unchanged') is not True
            or value.get('original_clocks_retained') is not True
            or value.get('final_strategy_delivery') is not False or value.get('email_sent') is not False
            or value.get('orders_placed') is not False
            or re.fullmatch('[0-9a-f]{40}',str(value.get('producer_commit',''))) is None):
        raise NoticePrefixError('notice_prefix_fixed_profile_differs')
    files,sizes=value.get('files'),value.get('sizes')
    if (not isinstance(files,dict) or not files or len(files)>MAX_FILES or not isinstance(sizes,dict)
            or set(files)!=set(sizes) or any(type(n) is not int or n<0 for n in sizes.values())
            or sum(sizes.values())>MAX_EXPANDED):
        raise NoticePrefixError('notice_prefix_manifest_not_closed')
    seen=set()
    for name,digest in files.items():
        if (not file_role(name) or name.casefold() in seen or re.fullmatch('[0-9a-f]{64}',str(digest)) is None
                or type(sizes[name]) is not int or sizes[name]<0):
            raise NoticePrefixError('notice_prefix_object_role_or_identity_differs')
        seen.add(name.casefold())
    for spec in RECEIPTS+[dict(path=START[0],sha256=START[1]),dict(path=END[0],sha256=END[1]),value['end_pointer_spec']]:
        if files.get(spec['path'])!=spec['sha256']:
            raise NoticePrefixError('notice_prefix_approved_spec_not_bound')
    baseline,payload=value.get('baseline_files'),value.get('payload_files')
    if (not isinstance(baseline,dict) or not isinstance(payload,dict)
            or set(baseline)&set(payload) or {**baseline,**payload}!=files
            or any(spec['path'] not in payload for spec in RECEIPTS+[dict(path=START[0]),dict(path=END[0]),dict(path=END_POINTER)])):
        raise NoticePrefixError('notice_prefix_explicit_baseline_partition_differs')
    return value


def borrowed_files(result):
    required={START[0],END[0],END_POINTER,*[r['path'] for r in RECEIPTS],*result.get('proof_files',{})}
    return {n:h for n,h in result.get('baseline_files',{}).items() if n not in required}


def validate_native_prefix(result,manifest):
    if (result.get('mode')!='prefix' or result.get('native_runtime') not in PRODUCER_READERS
            or result.get('previous_authority')!=START or result.get('authority')!=END
            or result.get('target')!=TARGET or result.get('receipt_count')!=2
            or result.get('old_prefix_unchanged') is not True or result.get('original_clocks_retained') is not True
            or result.get('files')!=manifest['files'] or result.get('sizes')!=manifest['sizes']
            or borrowed_files(result)!=manifest['baseline_files']):
        raise NoticePrefixError('notice_prefix_native_and_transport_closure_differ')


def validate_native_current(value):
    ref=value.get('authority')
    if (value.get('mode')!='current' or value.get('native_runtime') not in PRODUCER_READERS
            or value.get('state') not in ('advance','covered','covered_baseline_without_active_freshness') or value.get('old_prefix_unchanged') is not True
            or value.get('original_clocks_retained') is not True or not isinstance(ref,list) or len(ref)!=2
            or not file_role(ref[0]) or re.fullmatch('[0-9a-f]{64}',str(ref[1])) is None):
        raise NoticePrefixError('notice_prefix_private_authority_unknown')
    if value['state']=='advance' and ref!=START:
        raise NoticePrefixError('notice_prefix_expected_prior_authority_differs')
    return value


def export_prefix(*,root,bundle,issuer_head,native=real_native):
    root=Path(root).resolve();bundle=Path(bundle).resolve()
    if bundle.exists():raise NoticePrefixError('preserve_existing_notice_prefix_bundle')
    if bundle.is_relative_to(root):raise NoticePrefixError('notice_prefix_export_must_be_outside_runtime')
    runtime_role(root,PRODUCER)
    watched={p:(root/p).read_bytes() if (root/p).exists() else None for p in (POINTER,FRESHNESS)}
    result=native(root,root,'prefix')
    if result.get('native_runtime')!=PRODUCER:
        raise NoticePrefixError('fixed_notice_profile_export_requires_its_original_P2_runtime')
    files=result.get('files',{});sizes=result.get('sizes',{})
    baseline=borrowed_files(result)
    value=dict(schema=SCHEMA,profile=PROFILE,producer_runtime=PRODUCER,consumer_runtime=CONSUMER,
        target=TARGET,previous_authority=START,authority=END,receipts=RECEIPTS,
        active_freshness_spec=RECEIPTS[1],end_pointer_spec=dict(path=END_POINTER,sha256=files.get(END_POINTER)),
        files=files,sizes=sizes,baseline_files=baseline,payload_files={n:h for n,h in files.items() if n not in baseline},
        producer_commit=issuer_head,exported_at=datetime.now(timezone.utc).isoformat(),
        source_inputs_only=True,old_prefix_unchanged=True,original_clocks_retained=True,
        final_strategy_delivery=False,email_sent=False,orders_placed=False)
    validate_manifest(value);validate_native_prefix(result,value)
    bundle.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(bundle,'x',zipfile.ZIP_DEFLATED) as archive:
        for name,digest in value['payload_files'].items():
            raw=bound(root,name).read_bytes()
            if len(raw)!=sizes[name] or hashlib.sha256(raw).hexdigest()!=digest:
                raise NoticePrefixError('notice_prefix_source_changed_during_export')
            archive.writestr(name,raw)
        archive.writestr('manifest.json',encode(value))
    if any(((root/p).read_bytes() if (root/p).exists() else None)!=raw for p,raw in watched.items()):
        raise NoticePrefixError('notice_prefix_source_authority_changed_during_export')
    return dict(status='approved_notice_prefix_exported_source_only',bundle=str(bundle),sha256=sha_file(bundle),
                bytes=bundle.stat().st_size,files=len(files),baseline_files=len(baseline),
                payload_files=len(value['payload_files']),target=TARGET,producer_commit=issuer_head,
                native_verified=True,current_signal_ready=False,final_strategy_delivery=False)


def inspect_bundle(*,bundle,expected_sha,not_after):
    bundle=Path(bundle).resolve()
    if (date.fromisoformat(not_after).isoformat()<TARGET or re.fullmatch('[0-9a-f]{64}',str(expected_sha)) is None
            or not 0<bundle.stat().st_size<=MAX_BUNDLE or sha_file(bundle)!=expected_sha):
        raise NoticePrefixError('notice_prefix_outer_identity_or_date_differs')
    with zipfile.ZipFile(bundle) as archive:
        infos=archive.infolist();names=[x.filename for x in infos]
        if (len(infos)>MAX_FILES+1 or len(set(n.casefold() for n in names))!=len(names)
                or any(x.is_dir() or stat.S_IFMT(x.external_attr>>16)==stat.S_IFLNK for x in infos)
                or sum(x.file_size for x in infos)>MAX_EXPANDED+64*1024**2):
            raise NoticePrefixError('notice_prefix_archive_shape_invalid')
        if names.count('manifest.json')!=1 or archive.getinfo('manifest.json').file_size>64*1024**2:
            raise NoticePrefixError('notice_prefix_manifest_missing_or_oversized')
        value=validate_manifest(json.loads(archive.read('manifest.json')))
        if set(names)!={'manifest.json',*value['payload_files']}:
            raise NoticePrefixError('notice_prefix_unbound_or_missing_object')
        for name,digest in value['payload_files'].items():
            if archive.getinfo(name).file_size!=value['sizes'][name]:
                raise NoticePrefixError('notice_prefix_declared_size_differs')
            with archive.open(name) as source:
                if hashlib.file_digest(source,'sha256').hexdigest()!=digest:
                    raise NoticePrefixError('notice_prefix_original_object_changed')
        if json.loads(archive.read(END_POINTER))!={'manifest_path':END[0],'manifest_sha256':END[1]}:
            raise NoticePrefixError('notice_prefix_original_endpoint_pointer_differs')
        previous=START;completed=None
        for spec in RECEIPTS:
            proof=json.loads(archive.read(spec['path']))
            if proof.get('target')!=TARGET or proof.get('previous_authority')!=previous or proof.get('authority')!=END:
                raise NoticePrefixError('notice_prefix_original_receipt_endpoints_differ')
            try:
                start_clock=datetime.fromisoformat(proof['required_asof']);end_clock=datetime.fromisoformat(proof['completed_at'])
                if (start_clock.tzinfo is None or end_clock.tzinfo is None or not start_clock<=end_clock<=datetime.now(timezone.utc)
                        or completed is not None and start_clock<completed):raise ValueError('clock')
            except (KeyError,TypeError,ValueError):raise NoticePrefixError('notice_prefix_original_clock_order_differs') from None
            completed=end_clock;previous=END
    return value


def _same(files):
    return all((p.read_bytes() if p.exists() else None)==raw for p,raw in files.items())


def _pair(root):
    return {name:bound(root,name).read_bytes() if bound(root,name).exists() else None for name in (POINTER,FRESHNESS)}


def _write_pair_member(path,raw):
    path.parent.mkdir(parents=True,exist_ok=True)
    if raw is None:
        if path.exists():path.unlink()
    else:atomic(path,raw)


def _rollback_pair(root,old,wanted):
    current=_pair(root)
    if any(current[n] not in (old[n],wanted[n]) for n in old):
        raise NoticePrefixError('notice_prefix_rollback_owner_unknown_preserve_journal')
    for name in reversed(list(old)):
        if any(_pair(root)[n] not in (old[n],wanted[n]) for n in old):
            raise NoticePrefixError('notice_prefix_rollback_owner_changed_preserve_journal')
        if current[name]!=old[name]:_write_pair_member(bound(root,name),old[name])
    if _pair(root)!=old:raise NoticePrefixError('notice_prefix_rollback_readback_differs')


@contextmanager
def native_refresh_lock(root):
    path=bound(root,PREFIX+'REFRESH-owner.json').with_name('REFRESH.lock')
    path.parent.mkdir(parents=True,exist_ok=True)
    raw=str(os.getpid()).encode();fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    try:
        os.write(fd,raw);os.close(fd);fd=None
        yield
    finally:
        if fd is not None:os.close(fd)
        if path.exists() and path.read_bytes()==raw:path.unlink()
        else:raise NoticePrefixError('notice_prefix_native_lock_owner_changed')


def import_prefix(*,root,protected_root,state,pool,bundle,expected_sha,not_after,out,native=real_native):
    for role in (root,protected_root,state,out):
        p=Path(role).absolute()
        if any(x.is_symlink() or (hasattr(x,'is_junction') and x.is_junction()) for x in (p,*p.parents)):
            raise NoticePrefixError('notice_prefix_role_contains_link')
    root,protected,state,out=map(lambda p:Path(p).resolve(),(root,protected_root,state,out))
    logical=str(root).replace('\\','/').casefold().rstrip('/')
    if any(logical==live or logical.startswith(live+'/') for live in (
            'd:/codex/microcap-local-maintenance','d:/动量策略/微盘股对冲策略')):
        raise NoticePrefixError('notice_prefix_import_refuses_live_or_primary_runtime')
    if (root==state or not root.is_relative_to(state) or root==protected or root.is_relative_to(protected)
            or protected.is_relative_to(state) or state.is_relative_to(protected)
            or out.is_relative_to(root) or out.is_relative_to(protected)
            or root.is_relative_to(out) or protected.is_relative_to(out)):
        raise NoticePrefixError('notice_prefix_requires_private_separate_roles')
    for p in (root,protected,state,out):
        if any(x.is_symlink() or (hasattr(x,'is_junction') and x.is_junction()) for x in (p,*p.parents)):
            raise NoticePrefixError('notice_prefix_role_contains_link')
    runtime_role(root,PRODUCER);runtime_role(protected,CONSUMER);reader=validate_runtime(root,protected)
    consumer_reader=(subprocess.check_output(['git','-C',str(protected),'rev-parse','HEAD'],encoding='utf-8').strip()
                     if reader else CONSUMER)
    _,symbols,_=load_pool(pool,not_after)
    manifest=inspect_bundle(bundle=bundle,expected_sha=expected_sha,not_after=not_after)
    out.mkdir(parents=True,exist_ok=True)
    stage=out/('verified-'+expected_sha)
    if not stage.exists():
        if shutil.disk_usage(out).free<sum(manifest['sizes'][n] for n in manifest['payload_files'])+len(manifest['files'])*4096+4*1024**3:
            raise NoticePrefixError('notice_prefix_disk_reserve_insufficient')
        stage.mkdir()
        with zipfile.ZipFile(bundle) as archive:
            for name in manifest['payload_files']:
                path=bound(stage,name);path.parent.mkdir(parents=True,exist_ok=True)
                with archive.open(name) as source,path.open('xb') as destination:shutil.copyfileobj(source,destination,1024**2)
    if any(not bound(stage,n).is_file() or sha_file(bound(stage,n))!=h for n,h in manifest['payload_files'].items()):
        raise NoticePrefixError('notice_prefix_preserved_stage_changed')
    wanted={POINTER:bound(stage,END_POINTER).read_bytes(),FRESHNESS:bound(stage,RECEIPTS[1]['path']).read_bytes()}
    if json.loads(wanted[POINTER])!={'manifest_path':END[0],'manifest_sha256':END[1]}:
        raise NoticePrefixError('notice_prefix_endpoint_pointer_differs')
    with ExitStack() as locks:
        for name in ('writer.lock','worker-slot-0.lock','worker-slot-1.lock','price-worker.lock'):
            locks.enter_context(writer_lock(state/name))
        locks.enter_context(native_refresh_lock(root))
        refs=checked_bootstrap(root,protected,state,symbols,not_after)
        refs.update({protected/n:(protected/n).read_bytes() if (protected/n).exists() else None for n in (POINTER,FRESHNESS)})
        # Borrow only declared, authenticated immutable D10 objects.  A new
        # hard-link view gives the unchanged Native reader its full closure
        # without duplicating two gigabytes of already restored history.
        view=Path(locks.enter_context(tempfile.TemporaryDirectory(prefix='notice-view-',dir=out)))
        for name,digest in manifest['files'].items():
            source=bound(root,name) if name in manifest['baseline_files'] else bound(stage,name)
            if not source.is_file() or source.stat().st_size!=manifest['sizes'][name] or sha_file(source)!=digest:
                raise NoticePrefixError('notice_prefix_required_baseline_object_missing_or_changed')
            destination=bound(view,name);destination.parent.mkdir(parents=True,exist_ok=True)
            try:os.link(source,destination)
            except OSError as exc:raise NoticePrefixError('notice_prefix_readonly_view_requires_same_volume_links') from exc
        validate_native_prefix(native(root,view,'prefix'),manifest)
        roots=dict(root=str(root),protected=str(protected),state=str(state))
        transaction=out/'transaction.json';original=None
        if transaction.exists():
            journal=json.loads(transaction.read_bytes())
            if (journal.get('schema')!='microcap_notice_prefix_transaction_v1'
                    or journal.get('bundle_sha256')!=expected_sha or journal.get('roots')!=roots
                    or journal.get('status') not in ('intent','rolled_back','complete','reconciled')
                    or journal.get('desired_pair')!={n:hashlib.sha256(raw).hexdigest() for n,raw in wanted.items()}
                    or set(journal.get('original_pair',{}))!=set(wanted)):
                raise NoticePrefixError('notice_prefix_preserve_other_transaction')
            for index,name in enumerate(wanted):
                backup=journal['original_pair'][name]
                if backup.get('path')!='original-pair-'+str(index)+'.bin' or type(backup.get('exists')) is not bool:
                    raise NoticePrefixError('notice_prefix_original_pair_backup_role_differs')
            original={n:(out/journal['original_pair'][n]['path']).read_bytes() if journal['original_pair'][n]['exists'] else None for n in wanted}
            for n,raw in original.items():
                if (hashlib.sha256(raw).hexdigest() if raw is not None else None)!=journal['original_pair'][n]['sha256']:
                    raise NoticePrefixError('notice_prefix_original_pair_backup_changed')
            actual=_pair(root)
            if actual!=wanted and actual!=original and all(actual[n] in (original[n],wanted[n]) for n in wanted):
                _rollback_pair(root,original,wanted)
        current=validate_native_current(native(root,root,'current',profile_root=view))
        if not _same(refs):raise NoticePrefixError('notice_prefix_protected_reference_changed')
        if current['state']=='covered_baseline_without_active_freshness':
            # Bootstrap intentionally copies immutable authority objects only.
            # A complete consumer already at this authority is the baseline,
            # so it needs no older producer freshness to accept its own state.
            pointer_raw=bound(protected,POINTER).read_bytes()
            protected_ref=json.loads(pointer_raw)
            formal=json.loads(refs[protected/'outputs/top100_delivery_manifest.json'])
            if (protected_ref!={'manifest_path':current['authority'][0],'manifest_sha256':current['authority'][1]}
                    or formal.get('inputs',{}).get(POINTER) not in (
                        hashlib.sha256(pointer_raw).hexdigest(),
                        hashlib.sha256(pointer_raw.replace(b'\r\n',b'\n')).hexdigest())):
                raise NoticePrefixError('notice_prefix_missing_active_requires_actual_formal_consumer_authority')
        if current['state'] in ('covered','covered_baseline_without_active_freshness'):
            if transaction.exists() and _pair(root)==wanted and journal['status'] in ('intent','rolled_back'):
                atomic(transaction,encode(dict(journal,status='reconciled')))
            result=dict(status='skipped_notice_prefix_already_covered',authority=current['authority'],
                        private_pointer_changed=False,private_freshness_changed=False)
        else:
            if current.get('authority')!=START:raise NoticePrefixError('notice_prefix_expected_prior_authority_differs')
            old=_pair(root)
            if original is not None and old!=original:
                raise NoticePrefixError('notice_prefix_pending_transaction_owner_differs')
            if original is None:
                backups={}
                for index,(name,raw) in enumerate(old.items()):
                    path='original-pair-'+str(index)+'.bin'
                    if raw is not None:atomic(out/path,raw)
                    backups[name]=dict(path=path,exists=raw is not None,sha256=hashlib.sha256(raw).hexdigest() if raw is not None else None)
                journal=dict(schema='microcap_notice_prefix_transaction_v1',status='intent',roots=roots,
                    bundle_sha256=expected_sha,original_pair=backups,
                    desired_pair={n:hashlib.sha256(raw).hexdigest() for n,raw in wanted.items()})
                atomic(transaction,encode(journal))
            try:
                for name,digest in manifest['payload_files'].items():
                    path=bound(root,name);path.parent.mkdir(parents=True,exist_ok=True)
                    if path.exists():
                        if path.is_symlink() or sha_file(path)!=digest:raise NoticePrefixError('notice_prefix_conflicting_immutable_object')
                    else:
                        raw=bound(stage,name).read_bytes()
                        if hashlib.sha256(raw).hexdigest()!=digest:raise NoticePrefixError('notice_prefix_stage_changed_during_import')
                        atomic(path,raw)
                if _pair(root)!=old or not _same(refs):raise NoticePrefixError('notice_prefix_cas_or_reference_changed')
                _write_pair_member(root/POINTER,wanted[POINTER])
                if _pair(root)!={POINTER:wanted[POINTER],FRESHNESS:old[FRESHNESS]} or not _same(refs):raise NoticePrefixError('notice_prefix_freshness_owner_changed')
                _write_pair_member(root/FRESHNESS,wanted[FRESHNESS])
                if _pair(root)!=wanted or not _same(refs):raise NoticePrefixError('notice_prefix_commit_readback_differs')
                after=validate_native_current(native(root,root,'current',profile_root=view))
                if after.get('state')!='covered' or after.get('authority')!=END:
                    raise NoticePrefixError('notice_prefix_native_postcommit_failed')
            except BaseException:
                _rollback_pair(root,old,wanted)
                atomic(transaction,encode(dict(journal,status='rolled_back')))
                raise
            atomic(transaction,encode(dict(journal,status='complete')))
            result=dict(status='approved_notice_prefix_imported_source_only',authority=END,
                        private_pointer_changed=True,private_freshness_changed=True)
        if not _same(refs):raise NoticePrefixError('notice_prefix_final_protected_reference_changed')
        result.update(schema='microcap_notice_prefix_admission_v1',profile=PROFILE,bundle_sha256=expected_sha,
            target=TARGET,requested_not_after=not_after,producer_runtime=PRODUCER,consumer_runtime=CONSUMER,
            verification_runtime=reader or current['native_runtime'],
            verification_consumer_runtime=consumer_reader,
            producer_commit=manifest['producer_commit'],original_clocks_retained=True,
            actual_consumer_anchor_and_bootstrap_unchanged=True,source_inputs_only=True,
            fresh_for_requested_date=False,current_signal_ready=False,
            final_strategy_delivery=False,email_sent=False,orders_placed=False)
        atomic(out/'receipt.json',encode(result))
        return result


def load_config(path):
    import urllib.parse
    value=json.loads(Path(path).read_bytes())
    if set(value)!={'schema','owner','notice_prefix'} or value['schema']!=POINTER_SCHEMA or value['owner']!='local':
        raise NoticePrefixError('notice_prefix_pointer_schema_or_owner_differs')
    item=value['notice_prefix']
    if item is None:return None
    if set(item)!={'url','sha256','target','runtime_sha','producer_commit'}:
        raise NoticePrefixError('notice_prefix_release_pointer_fields_differ')
    url=urllib.parse.urlsplit(item['url'])
    if (url.scheme!='https' or url.netloc!='github.com' or url.query or url.fragment or '%' in url.path
            or '..' in url.path or not url.path.startswith('/liuruojiang/codex-daily-automation-probe/releases/download/microcap-notice-prefix-')
            or not url.path.endswith('.zip') or item['target']!=TARGET or item['runtime_sha']!=PRODUCER
            or re.fullmatch('[0-9a-f]{64}',str(item['sha256'])) is None
            or re.fullmatch('[0-9a-f]{40}',str(item['producer_commit'])) is None):
        raise NoticePrefixError('notice_prefix_release_pointer_identity_differs')
    return item


def select_forward(*,root,protected_root,state,pool,target,out,native=real_native):
    """Choose the original checkpoint for an upcoming Native advance.

    Same-authority self reviews stay immutable evidence.  This changes only
    the private active receipt, so Native backs up the real advancing receipt
    that its unchanged older-chain walker expects.  No authority advances here.
    """
    if date.fromisoformat(target).isoformat()!=target:
        raise NoticePrefixError('notice_forward_canonical_target_required')
    for role in (root,protected_root,state,out):
        p=Path(role).absolute()
        if any(x.is_symlink() or (hasattr(x,'is_junction') and x.is_junction()) for x in (p,*p.parents)):
            raise NoticePrefixError('notice_forward_role_contains_link')
    root,protected,state,out=map(lambda p:Path(p).resolve(),(root,protected_root,state,out))
    logical=str(root).replace('\\','/').casefold().rstrip('/')
    if (any(logical==live or logical.startswith(live+'/') for live in (
            'd:/codex/microcap-local-maintenance','d:/动量策略/微盘股对冲策略'))
            or root==state or not root.is_relative_to(state) or root.is_relative_to(protected)
            or state.is_relative_to(protected) or protected.is_relative_to(state)
            or out.is_relative_to(root) or out.is_relative_to(protected)
            or root.is_relative_to(out) or protected.is_relative_to(out)):
        raise NoticePrefixError('notice_forward_requires_private_separate_roles')
    runtime_role(root,PRODUCER);runtime_role(protected,CONSUMER);reader=validate_runtime(root,protected)
    consumer_reader=(subprocess.check_output(['git','-C',str(protected),'rev-parse','HEAD'],encoding='utf-8').strip()
                     if reader else CONSUMER)
    _,symbols,_=load_pool(pool,target)
    with ExitStack() as locks:
        for name in ('writer.lock','worker-slot-0.lock','worker-slot-1.lock','price-worker.lock'):
            locks.enter_context(writer_lock(state/name))
        locks.enter_context(native_refresh_lock(root))
        refs=checked_bootstrap(root,protected,state,symbols,target)
        refs.update({protected/n:(protected/n).read_bytes() if (protected/n).exists() else None for n in (POINTER,FRESHNESS)})
        before=_pair(root)
        if before[POINTER] is None:raise NoticePrefixError('notice_forward_current_pointer_missing')
        pointer=json.loads(before[POINTER]);authority=[pointer.get('manifest_path'),pointer.get('manifest_sha256')]
        result=native(root,root,'forward',requested_target=target)
        digest=hashlib.sha256(before[FRESHNESS]).hexdigest() if before[FRESHNESS] is not None else None
        if (result.get('mode')!='forward' or result.get('native_runtime') not in PRODUCER_READERS
                or result.get('authority')!=authority or result.get('requested_target')!=target
                or result.get('active_sha256')!=digest or result.get('state') not in ('select','noop')
                or result.get('old_prefix_unchanged') is not True or result.get('original_clocks_retained') is not True
                or not isinstance(result.get('authority_day'),str)
                or date.fromisoformat(result['authority_day']).isoformat()!=result['authority_day']):
            raise NoticePrefixError('notice_forward_native_selection_proof_incomplete')
        if _pair(root)!=before or not _same(refs):
            raise NoticePrefixError('notice_forward_native_read_changed_owner_or_reference')
        if before[FRESHNESS] is None and target>result['authority_day']:
            protected_pointer=refs.get(protected/POINTER)
            protected_ref=json.loads(protected_pointer) if protected_pointer is not None else {}
            if [protected_ref.get('manifest_path'),protected_ref.get('manifest_sha256')]!=authority:
                raise NoticePrefixError('notice_forward_missing_active_receipt_for_unhanded_source_authority')
        if (result['state']=='noop' and target>result['authority_day'] and before[FRESHNESS] is not None
                and json.loads(before[FRESHNESS]).get('previous_authority')==authority):
            raise NoticePrefixError('notice_forward_self_review_cannot_skip_upcoming_advance')
        out.mkdir(parents=True,exist_ok=True);intent_path=out/'selection-intent.json'
        roots=dict(root=str(root),protected=str(protected),state=str(state))
        receipt=dict(schema='microcap_notice_forward_selection_v1',status='notice_forward_checkpoint_not_needed',
            verification_runtime=reader or result['native_runtime'],verification_consumer_runtime=consumer_reader,
            requested_target=target,authority=authority,authority_day=result['authority_day'],reason=result.get('reason'),
            active_freshness_changed=False,authority_pointer_unchanged=True,
            original_receipt_bytes_and_clocks_preserved=True,actual_consumer_anchor_and_bootstrap_unchanged=True,
            target_freshness_granted=False,current_signal_ready=False,source_inputs_only=True,
            final_strategy_delivery=False,email_sent=False,orders_placed=False)
        if result['state']=='noop':
            if intent_path.exists():
                old=json.loads(intent_path.read_bytes())
                if (old.get('schema')=='microcap_notice_forward_intent_v1' and old.get('roots')==roots
                        and old.get('requested_target')==target and old.get('pointer_sha256')==hashlib.sha256(before[POINTER]).hexdigest()
                        and old.get('selected_spec')==result.get('selected_spec')
                        and old.get('selected_spec',{}).get('sha256')==digest):
                    backup=out/'original-active-self.json'
                    if (not backup.is_file() or sha_file(backup)!=old.get('original_active_sha256')
                            or not file_role(old.get('original_active_spec',{}).get('path',''))
                            or sha_file(bound(root,old['original_active_spec']['path']))!=old['original_active_sha256']):
                        raise NoticePrefixError('notice_forward_original_self_evidence_missing')
                    atomic(intent_path,encode(dict(old,status='reconciled')))
                    receipt.update(status='original_notice_forward_checkpoint_selected',selection_reconciled=True,
                                   selected_spec=result['selected_spec'],original_active_sha256=old['original_active_sha256'])
        else:
            if before[FRESHNESS] is None or not target>result['authority_day']:
                raise NoticePrefixError('notice_forward_selection_without_actual_forward')
            active=json.loads(before[FRESHNESS]);spec=result.get('selected_spec') or {};self_spec=result.get('active_spec') or {}
            expected_path=PurePosixPath(authority[0]).with_name('proof.json').as_posix()
            if (active.get('previous_authority')!=authority or active.get('authority')!=authority
                    or active.get('target')!=result['authority_day'] or spec.get('path')!=expected_path
                    or not file_role(spec.get('path','')) or not file_role(self_spec.get('path',''))
                    or self_spec.get('sha256')!=digest or bound(root,self_spec['path']).read_bytes()!=before[FRESHNESS]):
                raise NoticePrefixError('notice_forward_active_is_not_original_same_authority_self_review')
            wanted=bound(root,spec['path']).read_bytes();advancing=json.loads(wanted)
            if (hashlib.sha256(wanted).hexdigest()!=spec.get('sha256') or wanted==before[FRESHNESS]
                    or advancing.get('authority')!=authority or advancing.get('previous_authority')==authority
                    or advancing.get('target')!=result['authority_day']):
                raise NoticePrefixError('notice_forward_original_advancing_receipt_differs')
            source_files=result.get('source_files')
            if (not isinstance(source_files,dict) or source_files.get(spec['path'])!=spec['sha256']
                    or source_files.get(self_spec['path'])!=self_spec['sha256']):
                raise NoticePrefixError('notice_forward_both_original_native_proofs_required')
            for name,expected in source_files.items():
                if not file_role(name) or sha_file(bound(root,name))!=expected:
                    raise NoticePrefixError('notice_forward_native_chain_object_changed')
            intent=dict(schema='microcap_notice_forward_intent_v1',status='intent',roots=roots,requested_target=target,
                pointer_sha256=hashlib.sha256(before[POINTER]).hexdigest(),authority=authority,
                original_active_sha256=digest,original_active_spec=self_spec,selected_spec=spec,
                authority_pointer_unchanged=True,source_inputs_only=True,original_clocks_retained=True)
            backup=out/'original-active-self.json'
            if backup.exists() and backup.read_bytes()!=before[FRESHNESS]:
                raise NoticePrefixError('notice_forward_preserve_original_self_backup')
            if not backup.exists():atomic(backup,before[FRESHNESS])
            if intent_path.exists():
                old=json.loads(intent_path.read_bytes())
                if any(old.get(k)!=v for k,v in intent.items() if k!='status'):
                    raise NoticePrefixError('notice_forward_preserve_other_selection_intent')
            else:atomic(intent_path,encode(intent))
            if _pair(root)!=before or not _same(refs):raise NoticePrefixError('notice_forward_cas_owner_changed')
            write_reconciled=False
            try:_write_pair_member(bound(root,FRESHNESS),wanted)
            except BaseException:
                actual=_pair(root)
                if actual!={POINTER:before[POINTER],FRESHNESS:wanted} or not _same(refs):
                    atomic(intent_path,encode(dict(intent,status='write_not_confirmed')))
                    raise
                write_reconciled=True
            if (_pair(root)!={POINTER:before[POINTER],FRESHNESS:wanted} or not _same(refs)
                    or sha_file(bound(root,spec['path']))!=spec['sha256']
                    or bound(root,self_spec['path']).read_bytes()!=before[FRESHNESS]):
                raise NoticePrefixError('notice_forward_selected_checkpoint_readback_changed')
            atomic(intent_path,encode(dict(intent,status='complete',write_response_lost_reconciled=write_reconciled)))
            receipt.update(status='original_notice_forward_checkpoint_selected',active_freshness_changed=True,
                selected_spec=spec,original_active_spec=self_spec,original_active_sha256=digest,
                write_response_lost_reconciled=write_reconciled,native_forward_still_required=True)
        if before[POINTER]!=_pair(root)[POINTER] or not _same(refs):
            raise NoticePrefixError('notice_forward_changed_authority_or_protected_anchor')
        atomic(out/'receipt.json',encode(receipt))
        return receipt


def warm(*,config,root,protected_root,state,pool,target,out,native=real_native):
    import urllib.request
    item=load_config(config)
    if item is None:return dict(status='no_notice_prefix_configured',final_strategy_delivery=False)
    if date.fromisoformat(target).isoformat()<TARGET:raise NoticePrefixError('notice_prefix_from_future')
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=True);bundle=out/'notice-prefix.zip'
    if not bundle.exists():
        stream_download(urllib.request.Request(item['url']),bundle,expected_sha=item['sha256'])
    manifest=inspect_bundle(bundle=bundle,expected_sha=item['sha256'],not_after=target)
    if manifest['producer_commit']!=item['producer_commit']:raise NoticePrefixError('notice_prefix_release_issuer_differs')
    return import_prefix(root=root,protected_root=protected_root,state=state,pool=pool,bundle=bundle,
                         expected_sha=item['sha256'],not_after=target,out=out/'admission',native=native)


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='action',required=True)
    export=sub.add_parser('export');export.add_argument('--root',type=Path,required=True);export.add_argument('--bundle',type=Path,required=True);export.add_argument('--issuer-head',required=True)
    inspect=sub.add_parser('inspect');inspect.add_argument('--bundle',type=Path,required=True);inspect.add_argument('--sha256',required=True);inspect.add_argument('--not-after-date',required=True)
    for action in ('import','warm','select-forward'):
        p=sub.add_parser(action)
        for name in ('root','protected-root','state','pool','out'):p.add_argument('--'+name,type=Path,required=True)
        p.add_argument('--date',required=True)
        if action=='warm':p.add_argument('--config',type=Path,required=True)
        elif action=='import':p.add_argument('--bundle',type=Path,required=True);p.add_argument('--sha256',required=True)
    args=parser.parse_args()
    if args.action=='export':value=export_prefix(root=args.root,bundle=args.bundle,issuer_head=args.issuer_head)
    elif args.action=='inspect':value=inspect_bundle(bundle=args.bundle,expected_sha=args.sha256,not_after=args.not_after_date)
    else:
        common=dict(root=args.root,protected_root=args.protected_root,state=args.state,pool=args.pool,out=args.out)
        if args.action=='warm':value=warm(**common,config=args.config,target=args.date)
        elif args.action=='select-forward':value=select_forward(**common,target=args.date)
        else:value=import_prefix(**common,bundle=args.bundle,expected_sha=args.sha256,not_after=args.date)
    print(json.dumps(value,ensure_ascii=False))


if __name__=='__main__':main()
