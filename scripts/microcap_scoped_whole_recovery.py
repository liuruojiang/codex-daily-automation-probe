"""Recover scoped whole-state artifacts without relabelling their release identity.

Successful main producers and strictly reconciled closed publications are discoverable. An explicitly
hash-pinned release seed is used only after a complete, unambiguous artifact miss.
All state validation and replacement stay in the original native restore.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from prepare_microcap_local import atomic, encode
from restore_microcap_verified_state import api_request, StripCrossOriginAuthRedirectHandler
from restore_approved_microcap_state import MAX_BUNDLE_BYTES, DOWNLOAD_CHUNK_BYTES
from restore_microcap_member_checkpoint import retry_checkpoint_read

REPOSITORY = 'liuruojiang/codex-daily-automation-probe'
WORKFLOW = '.github/workflows/microcap-member-maintenance.yml'
UPLOAD_STEP = 'Publish verified scoped whole state'
PREFIX = 'microcap-scoped-whole-v1-'
BUNDLE_NAME = 'microcap-whole-delivery-state.zip'
MANIFEST = 'outputs/top100_delivery_manifest.json'
MAX_MANIFEST_BYTES = 16 * 1024 * 1024


class RecoveryError(RuntimeError):
    """A controlled non-secret diagnostic code, safe to put in a receipt."""


def artifact_name(runtime_sha):
    if not isinstance(runtime_sha,str) or re.fullmatch('[0-9a-f]{40}',runtime_sha) is None:
        raise RecoveryError('exact_runtime_sha_required')
    return PREFIX+runtime_sha


def file_sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def api_json(url,token):
    def read():
        with urllib.request.urlopen(api_request(url,token),timeout=30) as response:
            return json.load(response)
    try:
        return retry_checkpoint_read(read,'scoped whole metadata request')
    except urllib.error.HTTPError as exc:
        raise RecoveryError('github_api_http_'+str(exc.code)) from None
    except (urllib.error.URLError,OSError,ValueError):
        raise RecoveryError('github_api_response_unavailable') from None


def eligible_run(run,run_id,*,allow_reconciliation=False):
    return (isinstance(run,dict) and run.get('id')==run_id and run.get('status')=='completed'
            and (run.get('conclusion')=='success' or (allow_reconciliation
                 and run.get('event')=='workflow_dispatch' and run.get('conclusion') in ('failure','cancelled','timed_out')))
            and run.get('head_branch')=='main'
            and run.get('path')==WORKFLOW
            and (run.get('repository') or {}).get('full_name')==REPOSITORY
            and (run.get('head_repository') or {}).get('full_name')==REPOSITORY
            and re.fullmatch('[0-9a-f]{40}',str(run.get('head_sha',''))) is not None)


def published_step(base,token,run,*,allow_reconciliation=False,observation=None):
    found=False
    for page in range(1,101):
        payload=api_json(f"{base}/runs/{run['id']}/jobs?per_page=100&page={page}",token)
        jobs=payload.get('jobs') if isinstance(payload,dict) else None
        if not isinstance(jobs,list):raise RecoveryError('github_jobs_response_invalid')
        for job in jobs:
            if (not isinstance(job,dict) or job.get('run_id')!=run['id']
                    or job.get('status')!='completed'):
                continue
            steps=job.get('steps',[])
            if job.get('conclusion')=='success' and any(isinstance(step,dict) and step.get('name')==UPLOAD_STEP
                   and step.get('status')=='completed' and step.get('conclusion')=='success'
                   for step in steps):
                found=True
                if observation is not None:observation['kind']='published_step_success'
            if not found and allow_reconciliation:
                succeeded={step.get('name') for step in steps if step.get('conclusion')=='success'}
                earned=(('Pack verified scoped whole state' in succeeded
                         and 'Earn original formal rebalance and next-session handoff' in succeeded)
                        or {'Publishable copy of verified restored archive','Verify complete restored three-version archive without sending'}<=succeeded
                        or 'Validate preserved earned whole archive without refreshing' in succeeded)
                if earned:
                    for step in steps:
                        if step.get('name')!=UPLOAD_STEP or step.get('status')!='completed':continue
                        if step.get('conclusion')=='success':accepted=True
                        elif step.get('conclusion') in ('failure','cancelled','timed_out'):
                            try:
                                stamp=datetime.fromisoformat(step['started_at'].replace('Z','+00:00'))
                                accepted=stamp.tzinfo is not None and stamp.year>=2020
                            except (KeyError,TypeError,ValueError):accepted=False
                        else:accepted=False
                        if accepted:
                            found=True
                            if observation is not None:observation['kind']='closed_server_object_reconciled_after_publication_response_loss'
        if len(jobs)<100:return found
    raise RecoveryError('github_job_pagination_incomplete')


def fetch_latest(token,runtime_sha):
    if not token:raise RecoveryError('github_repository_authentication_required')
    base=f'https://api.github.com/repos/{REPOSITORY}/actions'
    name=artifact_name(runtime_sha);candidates=[]
    for page in range(1,101):
        payload=api_json(f'{base}/artifacts?name={urllib.parse.quote(name)}&per_page=100&page={page}',token)
        items=payload.get('artifacts') if isinstance(payload,dict) else None
        if not isinstance(items,list):raise RecoveryError('github_artifact_response_invalid')
        candidates.extend(item for item in items if isinstance(item,dict) and item.get('name')==name and item.get('expired') is False)
        if len(items)<100:break
    else:raise RecoveryError('github_artifact_pagination_incomplete')
    for item in sorted(candidates,key=lambda a:(str(a.get('created_at','')),int(a.get('id',0))),reverse=True):
        run_id=(item.get('workflow_run') or {}).get('id')
        if type(run_id) is not int or run_id<=0:continue
        run=api_json(f'{base}/runs/{run_id}',token)
        if not eligible_run(run,run_id,allow_reconciliation=True):continue
        reconcile=(run.get('event')=='workflow_dispatch'
                   and (item.get('workflow_run') or {}).get('head_sha')==run['head_sha']
                   and re.fullmatch('sha256:[0-9a-f]{64}',str(item.get('digest',''))) is not None)
        publication={}
        if not published_step(base,token,run,allow_reconciliation=reconcile,observation=publication):continue
        if (type(item.get('id')) is not int or item['id']<=0
                or item.get('archive_download_url')!=f"{base}/artifacts/{item['id']}/zip"):
            raise RecoveryError('artifact_repository_url_mismatch')
        if type(item.get('size_in_bytes')) is not int or not 0<item['size_in_bytes']<=MAX_BUNDLE_BYTES:
            raise RecoveryError('artifact_size_out_of_bounds')
        digest=item.get('digest')
        if digest is not None and re.fullmatch('sha256:[0-9a-f]{64}',str(digest)) is None:
            raise RecoveryError('artifact_digest_invalid')
        declared=(item.get('workflow_run') or {}).get('head_sha')
        if declared is not None and declared!=run['head_sha']:
            raise RecoveryError('artifact_workflow_identity_mismatch')
        return dict(item,producer_head_sha=run['head_sha'],publication_acceptance=publication['kind'])
    return None


class HTTPSAuthSafeRedirect(StripCrossOriginAuthRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        parsed=urllib.parse.urlsplit(newurl)
        if parsed.scheme!='https' or not parsed.netloc or parsed.username or parsed.password:
            raise RecoveryError('download_redirect_not_https')
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def stream_download(request,destination,*,expected_sha=None,expected_bytes=None):
    destination=Path(destination)
    destination.parent.mkdir(parents=True,exist_ok=True)
    if destination.exists():raise RecoveryError('download_destination_already_exists')
    digest=hashlib.sha256();count=0
    opener=urllib.request.build_opener(HTTPSAuthSafeRedirect())
    try:
        with opener.open(request,timeout=60) as response, destination.open('xb') as target:
            while chunk:=response.read(DOWNLOAD_CHUNK_BYTES):
                count+=len(chunk)
                if count>MAX_BUNDLE_BYTES or (expected_bytes is not None and count>expected_bytes):
                    raise RecoveryError('download_body_exceeds_limit')
                digest.update(chunk);target.write(chunk)
    except urllib.error.HTTPError as exc:
        raise RecoveryError('download_http_'+str(exc.code)) from None
    except (urllib.error.URLError,OSError):
        raise RecoveryError('download_transport_unavailable') from None
    if count==0 or (expected_bytes is not None and count!=expected_bytes):
        raise RecoveryError('download_body_incomplete')
    actual=digest.hexdigest()
    if expected_sha is not None and actual!=expected_sha:
        raise RecoveryError('download_digest_mismatch')
    return dict(sha256=actual,bytes=count)


def download_artifact(item,token,directory):
    directory=Path(directory)
    outer=directory/'github-artifact.zip'
    digest=item.get('digest')
    transport=stream_download(api_request(item['archive_download_url'],token),outer,
                              expected_sha=digest.split(':',1)[1] if digest else None,
                              expected_bytes=item['size_in_bytes'])
    inner=directory/BUNDLE_NAME
    with zipfile.ZipFile(outer) as archive:
        infos=archive.infolist()
        if (len(infos)!=1 or infos[0].filename!=BUNDLE_NAME or infos[0].is_dir()
                or stat.S_ISLNK(infos[0].external_attr>>16)
                or not 0<infos[0].file_size<=MAX_BUNDLE_BYTES):
            raise RecoveryError('artifact_outer_archive_invalid')
        count=0
        with archive.open(infos[0]) as source,inner.open('xb') as target:
            while chunk:=source.read(DOWNLOAD_CHUNK_BYTES):
                count+=len(chunk)
                if count>MAX_BUNDLE_BYTES:raise RecoveryError('artifact_inner_bundle_exceeds_limit')
                target.write(chunk)
        if count!=infos[0].file_size:raise RecoveryError('artifact_inner_bundle_incomplete')
    return inner,dict(github_artifact_sha256=transport['sha256'],github_artifact_bytes=transport['bytes'],
                      github_digest_verified=digest is not None)


def validate_seed(url,expected):
    parsed=urllib.parse.urlsplit(url)
    prefix='/'+REPOSITORY+'/releases/download/'
    if (parsed.scheme!='https' or parsed.netloc!='github.com' or parsed.username or parsed.password
            or parsed.query or parsed.fragment or not parsed.path.startswith(prefix)
            or len(parsed.path.split('/'))!=7 or '%' in parsed.path or '\\' in parsed.path
            or any(p in ('','.', '..') for p in parsed.path[len(prefix):].split('/'))):
        raise RecoveryError('fallback_requires_exact_repository_https_release_asset')
    if not isinstance(expected,str) or re.fullmatch('[0-9a-f]{64}',expected) is None:
        raise RecoveryError('fallback_requires_exact_sha256')


def bundle_identity(bundle,runtime_sha,not_after):
    artifact_name(runtime_sha)
    bundle=Path(bundle);limit=date.fromisoformat(not_after)
    if not 0<bundle.stat().st_size<=MAX_BUNDLE_BYTES:raise RecoveryError('whole_bundle_size_out_of_bounds')
    with zipfile.ZipFile(bundle) as archive:
        if archive.namelist().count(MANIFEST)!=1:raise RecoveryError('whole_manifest_missing_or_duplicated')
        info=archive.getinfo(MANIFEST)
        if info.file_size>MAX_MANIFEST_BYTES:raise RecoveryError('whole_manifest_exceeds_limit')
        raw=archive.read(info);manifest=json.loads(raw)
    if (not isinstance(manifest,dict) or manifest.get('status')!='complete' or manifest.get('ok') is not True
            or manifest.get('scope')!='whole_workspace_delivery'
            or re.fullmatch('[0-9a-f]{40}',str(manifest.get('release_sha',''))) is None
            or any(not isinstance(manifest.get(key),dict) or not manifest[key] for key in ('inputs','artifacts','streams'))):
        raise RecoveryError('whole_manifest_not_formal_complete')
    seed=date.fromisoformat(manifest['expected_date'])
    if seed.isoformat()!=manifest['expected_date'] or seed>limit:raise RecoveryError('whole_seed_after_target')
    return dict(runtime_sha=runtime_sha,source_manifest_release_sha=manifest['release_sha'],seed_date=seed.isoformat(),
                source_manifest_sha256=hashlib.sha256(raw).hexdigest(),bundle_sha256=file_sha(bundle),bundle_bytes=bundle.stat().st_size)


VERIFY = r'''
import subprocess,sys
from pathlib import Path
root=Path(sys.argv[1]).resolve();runtime=sys.argv[2];release=sys.argv[3]
if subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()!=runtime:
    raise ValueError('Exact scoped recovery runtime differs')
available=subprocess.run(['git','-C',str(root),'cat-file','-e',release+'^{commit}'],capture_output=True)
if available.returncode:
    fetched=subprocess.run(['git','-C',str(root),'fetch','--no-tags','origin',release],capture_output=True)
    if fetched.returncode:raise ValueError('Exact historical release object is unavailable')
sys.path.insert(0,str(root))
from scripts.top100_delivery import _verify_release_files
if _verify_release_files(root,release)!=release:raise ValueError('Historical release object identity differs')
'''

RESTORE = r'''
import sys,json
from pathlib import Path
root=Path(sys.argv[1]).resolve();sys.path.insert(0,str(root))
sys.path.insert(0,sys.argv[5])
from microcap_native_staged_validation import restore_isolated
report=restore_isolated(root,Path(sys.argv[2]),sys.argv[3],Path(sys.argv[4]).parent/'native-staged-validation')
if report.get('ok') is not True:raise ValueError('Native whole-state restore rejected its bundle')
Path(sys.argv[4]).write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
'''


def native_stage(label,code,args,work):
    with (work/(label+'.stdout.log')).open('wb') as stdout,(work/(label+'.stderr.log')).open('wb') as stderr:
        process=subprocess.run([sys.executable,'-B','-c',code,*map(str,args)],stdout=stdout,stderr=stderr)
    if process.returncode:raise RecoveryError('native_'+label+'_failed')


def restore_bundle(root,bundle,runtime_sha,not_after,work):
    root,bundle,work=Path(root).resolve(),Path(bundle).resolve(),Path(work).resolve()
    identity=bundle_identity(bundle,runtime_sha,not_after)
    native_stage('source-release',VERIFY,[root,runtime_sha,identity['source_manifest_release_sha']],work)
    native_stage('whole-restore',RESTORE,[root,bundle,identity['seed_date'],work/'native-restore.json',Path(__file__).resolve().parent],work)
    restored=json.loads((root/MANIFEST).read_bytes())
    actual=restored.get('release_sha','')
    if re.fullmatch('[0-9a-f]{40}',str(actual)) is None:raise RecoveryError('restored_release_identity_invalid')
    native_stage('restored-release',VERIFY,[root,runtime_sha,actual],work)
    if file_sha(bundle)!=identity['bundle_sha256']:raise RecoveryError('whole_bundle_changed_during_restore')
    return dict(identity,restored=True,restored_manifest_release_sha=actual,
                native_restore=json.loads((work/'native-restore.json').read_bytes()),
                historical_release_files_equivalent=True,freshness='verified_archive_only_requires_current_refresh')


def recover(*,root,runtime_sha,not_after,bundle,receipt,token,fallback_url=None,fallback_sha256=None,require_artifact=False):
    root,bundle,receipt=map(lambda p:Path(p).resolve(),(root,bundle,receipt))
    artifact_name(runtime_sha);date.fromisoformat(not_after)
    if fallback_url is not None or fallback_sha256 is not None:
        validate_seed(fallback_url or '',fallback_sha256 or '')
    if require_artifact and fallback_url:raise RecoveryError('required_artifact_cannot_use_fallback')
    if receipt.exists() or bundle.exists():raise RecoveryError('recovery_evidence_destination_already_exists')
    if receipt==bundle:raise RecoveryError('recovery_receipt_cannot_replace_bundle')
    if (bundle==root or receipt==root or bundle.is_relative_to(root) or receipt.is_relative_to(root)
            or root.is_relative_to(bundle) or root.is_relative_to(receipt)):
        raise RecoveryError('recovery_evidence_must_be_outside_strategy_checkout')
    actual=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
    if actual!=runtime_sha:raise RecoveryError('checkout_differs_from_exact_runtime')
    bundle.parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='scoped-whole-recovery-',dir=bundle.parent))
    source=dict(runtime_sha=runtime_sha,repository=REPOSITORY,restored=False,fallback_used=False,work_directory=str(work))
    try:
        item=fetch_latest(token,runtime_sha)
        if item is None:
            if require_artifact or not fallback_url:raise RecoveryError('no_eligible_published_scoped_whole_artifact')
            source.update(source='explicit_release_seed',fallback_used=True,fallback_reason='complete_discovery_no_eligible_artifact',
                          seed_url=fallback_url,seed_sha256=fallback_sha256)
            downloaded=work/BUNDLE_NAME
            stream_download(fallback_url,downloaded,expected_sha=fallback_sha256)
            transport={}
        else:
            source.update(source='published_scoped_whole_artifact',source_run=item['workflow_run']['id'],
                          artifact_id=item['id'],artifact_name=item['name'],producer_head_sha=item['producer_head_sha'])
            if item.get('publication_acceptance'):
                source['publication_acceptance']=item['publication_acceptance']
            downloaded,transport=download_artifact(item,token,work)
        # Preserve the original bytes and manifest release identity. No repack.
        os.replace(downloaded,bundle)
        result=dict(source,**transport,**restore_bundle(root,bundle,runtime_sha,not_after,work),status='scoped_whole_restore_verified')
        atomic(receipt,encode(result))
        return result
    except Exception as exc:
        failure=dict(source,status='failed',error_code=str(exc) if isinstance(exc,RecoveryError) else type(exc).__name__)
        atomic(receipt,encode(failure))
        raise RecoveryError(failure['error_code']) from None


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('root','bundle','receipt'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--runtime-sha',required=True);parser.add_argument('--not-after-date',required=True)
    parser.add_argument('--fallback-url');parser.add_argument('--fallback-sha256');parser.add_argument('--require-artifact',action='store_true')
    args=parser.parse_args()
    if os.environ.get('GITHUB_REPOSITORY',REPOSITORY)!=REPOSITORY:
        parser.error('Scoped recovery is limited to its exact repository')
    try:
        report=recover(root=args.root,runtime_sha=args.runtime_sha,not_after=args.not_after_date,bundle=args.bundle,
                       receipt=args.receipt,token=os.environ.get('GITHUB_TOKEN',''),fallback_url=args.fallback_url,
                       fallback_sha256=args.fallback_sha256,require_artifact=args.require_artifact)
    except Exception as exc:
        print(json.dumps(dict(restored=False,error_code=str(exc) if isinstance(exc,RecoveryError) else type(exc).__name__)))
        return 1
    print(json.dumps(report));return 0


if __name__=='__main__':raise SystemExit(main())
