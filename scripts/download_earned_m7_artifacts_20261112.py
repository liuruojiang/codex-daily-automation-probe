"""Recover the two immutable original artifacts; do not rerun their producer."""
from pathlib import Path
import hashlib,json,shutil,subprocess,zipfile

REPO='liuruojiang/codex-daily-automation-probe'
RUN=43149241199
HEAD='1d152fbd5fac2607d6f4cc9e50b0a864a0e91a5c'
ARTIFACTS=(
 (13913848136,'microcap-source-observation-recertification-20261112',1670136,
  '8f002264206d770df4771695298716984885a40f5a2916fcb5f3a9591b4a7742',7775465,'first-run-evidence'),
 (13914725127,'microcap-source-observation-publication-recovery-20261112',601468302,
  '2e61805d24359a88e4a61d4bf1cd8944d34ec64e3e37ea8b13b7bdfcd9f156c6',672177030,'earned-archive'),
)
base=Path('transport').resolve();source=base/'original-artifacts';source.mkdir(parents=True,exist_ok=True)
receipts=[]
for artifact_id,name,size,digest,expanded,folder in ARTIFACTS:
 meta=json.loads(subprocess.check_output(['gh','api',f'repos/{REPO}/actions/artifacts/{artifact_id}']))
 assert (meta['id'],meta['name'],meta['size_in_bytes'],meta['digest'],meta['expired'])==(artifact_id,name,size,'sha256:'+digest,False)
 assert meta['workflow_run']['id']==RUN and meta['workflow_run']['head_sha']==HEAD
 archive=source/(str(artifact_id)+'.zip')
 if not archive.exists():
  with archive.open('xb') as stream:
   subprocess.run(['gh','api',f'repos/{REPO}/actions/artifacts/{artifact_id}/zip'],stdout=stream,check=True)
 with archive.open('rb') as stream:assert hashlib.file_digest(stream,'sha256').hexdigest()==digest
 assert archive.stat().st_size==size
 dest=base/folder;dest.mkdir(exist_ok=True)
 with zipfile.ZipFile(archive) as payload:
  infos=payload.infolist();assert sum(i.file_size for i in infos)==expanded
  assert len({i.filename for i in infos})==len(infos)
  if folder=='earned-archive':
   assert len(infos)==1 and infos[0].filename=='microcap-whole-delivery-state-archive-20261110-m7-observation.zip'
  for info in infos:
   path=(dest/info.filename).resolve()
   assert path.is_relative_to(dest) and not (info.external_attr>>16&0o170000)==0o120000
   if info.is_dir():path.mkdir(parents=True,exist_ok=True);continue
   assert not path.exists(),'Preserve prior recovery instead of overwriting'
   path.parent.mkdir(parents=True,exist_ok=True)
   with payload.open(info) as incoming,path.open('xb') as outgoing:shutil.copyfileobj(incoming,outgoing)
 receipts.append(dict(artifact_id=artifact_id,sha256=digest,bytes=size,expanded=expanded,folder=folder))
(base/'original-artifact-downloads.json').write_text(json.dumps(dict(source_run=RUN,source_head=HEAD,artifacts=receipts),indent=2),encoding='utf8')
print(json.dumps(receipts))
