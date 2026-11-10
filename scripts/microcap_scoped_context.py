"""Transport original scoped ST/name receipts without collecting or retiming them.

An independent producer-code checkout receives data only. Its completed readback
provides a portable configuration to the separate native candidate consumer.
"""
from __future__ import annotations

import argparse
from datetime import date
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import zipfile

from prepare_microcap_local import atomic, encode, sha
from maintain_microcap_local import bound, RUNTIME
from microcap_scoped_inputs import read_bundle, REVIEWED_CONSUMERS, MAX_BYTES

SCHEMA = 'microcap_scoped_original_context_v1'
RECEIPT = 'maintenance-evidence/producer-metadata.json'
POINTER = '.microcap_index_cache/st_notice_archive_pointer.json'
NAMES = '.microcap_index_cache/local_current_names/'
MAX_CONTEXT_EXPANDED = 3 * 1024 * 1024 * 1024


class OriginalObjects:
    """Keep the authenticated compressed archive; stream original objects."""
    def __init__(self, raw, files):
        self.raw=raw;self.files=files

    def __len__(self):
        return len(self.files)

    def items(self):
        with zipfile.ZipFile(io.BytesIO(self.raw)) as archive:
            for name,digest in self.files.items():
                body=archive.read(name)
                if sha(body)!=digest:
                    raise ValueError('Context original object SHA differs')
                yield name,body


def permitted(name):
    p = PurePosixPath(name)
    if (not isinstance(name,str) or p.is_absolute() or '..' in p.parts or ':' in name
            or '\\' in name or p.as_posix()!=name):
        return False
    if name in (RECEIPT, POINTER, '.microcap_index_cache/current_st.csv',
                '.microcap_index_cache/security_master.csv', '.microcap_index_cache/st_notice_refresh_proof.json'):
        return True
    if name.startswith(NAMES):
        return len(p.parts)==3 and re.fullmatch(r'\d{4}-\d{2}-\d{2}\.json',p.name) is not None
    if name.startswith('.microcap_index_cache/security_meta/'):
        return len(p.parts)==3 and re.fullmatch(r'\d{6}\.json',p.name) is not None
    return (name.startswith('.microcap_index_cache/st_notice_sources/')
            and p.suffix in ('.json','.csv','.raw','.txt','.html','.pdf'))


def load_reader(root):
    root=Path(root).resolve()
    actual=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
    if actual not in REVIEWED_CONSUMERS:
        raise ValueError('Context reader requires an immutable reviewed consumer')
    sys.path.insert(0,str(root))
    from scripts.top100_delivery import verify_candidate_release
    from scripts import scoped_native_consumer as native
    verify_candidate_release(root,actual)
    return native


def export(source, reader, config_path, previous, destination):
    source=Path(source).resolve();config=json.loads(Path(config_path).read_bytes())
    native=load_reader(reader)
    source_manifest,_,_=read_bundle(config['bundle'],'9999-12-31',config['bundle_sha256'])
    _,name=native.checked_producer(source,config,source_manifest)
    chain,specs=native.original_notice_chain(source,previous,source_manifest['target'])
    receipt_raw=Path(config['producer_metadata_receipt']).read_bytes()
    receipt=json.loads(receipt_raw)
    files={**receipt['files'],**chain['source_files']}
    # A freshness receipt validates each predecessor against its original
    # archive. Those base objects are already present in a warm consumer, but
    # must be carried explicitly to a fresh independent checkout.
    from scripts.st_history_scope import source_closure
    references=[previous]
    references += [json.loads(bound(source,s['path']).read_bytes())['authority'] for s in specs]
    for reference in references:
        for name,digest in source_closure(source,*reference).items():
            if name in files and files[name]!=digest:
                raise ValueError('Original notice archives have conflicting object identities')
            files[name]=digest
    from scripts.st_notice_refresh import FRESHNESS
    for item in (name,POINTER,FRESHNESS,'.microcap_index_cache/security_master.csv'):
        files[item]=sha(bound(source,item).read_bytes())
    bodies={name:bound(source,name).read_bytes() for name in files}
    bodies[RECEIPT]=receipt_raw;files[RECEIPT]=sha(receipt_raw)
    unsupported=[name for name in files if not permitted(name)]
    if unsupported:
        raise ValueError('Original context has unsupported transport roles: '+repr(unsupported[:10]))
    if any(sha(bodies[name])!=digest for name,digest in files.items()):
        raise ValueError('Original context changed during export')
    manifest=dict(schema=SCHEMA,target=source_manifest['target'],producer_runtime=RUNTIME,
        source_bundle_sha256=config['bundle_sha256'],source_manifest=source_manifest,
        previous_authority=previous,original_notice_receipts=specs,files=files,
        metadata_receipt_sha256=sha(receipt_raw),final_strategy_delivery=False,
        original_observation_clocks_preserved=True)
    destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(destination,'x',zipfile.ZIP_DEFLATED) as archive:
        for name,raw in bodies.items():archive.writestr(name,raw)
        archive.writestr('manifest.json',encode(manifest))
    return dict(status='original_context_exported',sha256=sha(destination.read_bytes()),
                files=len(files),target=manifest['target'],final_strategy_delivery=False)


def read_context(path,expected,*,lazy=False):
    path=Path(path)
    if not 0<path.stat().st_size<=MAX_BYTES:
        raise ValueError('Context archive size or SHA differs')
    raw=path.read_bytes()
    if len(raw)>MAX_BYTES or sha(raw)!=expected:
        raise ValueError('Context archive size or SHA differs')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names=archive.namelist()
        if len(names)!=len(set(names)) or 'manifest.json' not in names:
            raise ValueError('Context archive has duplicate or absent manifest')
        if (len(names)>50000 or sum(i.file_size for i in archive.infolist())>MAX_CONTEXT_EXPANDED
                or any(i.file_size>32*1024*1024 for i in archive.infolist())):
            raise ValueError('Context archive expands beyond limit')
        manifest=json.loads(archive.read('manifest.json'))
        if (manifest.get('schema')!=SCHEMA or manifest.get('producer_runtime')!=RUNTIME
                or manifest.get('final_strategy_delivery') is not False
                or manifest.get('original_observation_clocks_preserved') is not True):
            raise ValueError('Context identity or scope differs')
        date.fromisoformat(manifest['target'])
        files=manifest['files']
        if set(names)!={'manifest.json',*files} or any(not permitted(n) for n in files):
            raise ValueError('Context has an undeclared or unsafe file role')
        objects=OriginalObjects(raw,files)
        bodies=objects if lazy else dict(objects.items())
    return manifest,bodies


def restore(root,reader,bundle,expected,source_bundle,config_out,*,resume=False):
    root=Path(root).resolve()
    actual=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
    if actual!=RUNTIME:
        raise ValueError('Original context requires the exact producer-code checkout')
    if any((root/'outputs'/n).exists() for n in (
            'microcap_top100_mom16_biweekly_live_v2_0_base_proxy_meta.json',
            'top100_delivery_manifest.json','top100_candidate_delivery_manifest.json')):
        raise ValueError('Refuse to install producer context into a strategy-state workspace')
    marker=root/'maintenance-evidence/context-readback.json'
    if marker.exists() or ((root/RECEIPT).exists() and not resume):
        raise ValueError('Use a fresh independent context checkout; existing evidence is preserved')
    native=load_reader(reader)
    manifest,bodies=read_context(bundle,expected,lazy=True)
    source_manifest,_,_=read_bundle(source_bundle,manifest['target'],manifest['source_bundle_sha256'])
    if source_manifest!=manifest['source_manifest']:
        raise ValueError('Context is bound to another stock-source bundle')
    # Data only; no archive path can install code, a NAV or a strategy manifest.
    for name,raw in bodies.items():
        path=bound(root,name)
        if path.exists():
            if path.read_bytes()!=raw:
                raise ValueError('Existing context object differs; preserved for review')
        else:
            atomic(path,raw)
    config=dict(schema='microcap_native_scoped_consumer_v1',source_root=str(root),
        bundle=str(Path(source_bundle).resolve()),bundle_sha256=manifest['source_bundle_sha256'],
        producer_metadata_receipt=str(root/RECEIPT),
        producer_metadata_receipt_sha256=manifest['metadata_receipt_sha256'])
    native.checked_producer(root,config,source_manifest)
    chain,specs=native.original_notice_chain(root,manifest['previous_authority'],manifest['target'])
    if specs!=manifest['original_notice_receipts']:
        raise ValueError('Restored original notice chain differs')
    if any(sha(bound(root,name).read_bytes())!=digest for name,digest in manifest['files'].items()):
        raise ValueError('Context changed during independent native readback')
    result=dict(status='original_context_native_readback_passed',context_sha256=expected,
        source_bundle_sha256=manifest['source_bundle_sha256'],target=manifest['target'],
        files=len(bodies),original_observation_clocks_preserved=True,
        native_notice_receipts=chain['receipt_count'],final_strategy_delivery=False)
    atomic(marker,encode(result));atomic(Path(config_out),encode(config))
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('export','restore'))
    p.add_argument('--root',type=Path,required=True);p.add_argument('--reader',type=Path,required=True)
    p.add_argument('--bundle',type=Path,required=True)
    p.add_argument('--config',type=Path);p.add_argument('--previous-authority',nargs=2)
    p.add_argument('--sha256');p.add_argument('--source-bundle',type=Path);p.add_argument('--config-out',type=Path)
    p.add_argument('--resume',action='store_true')
    a=p.parse_args()
    if a.action=='export':
        if a.config is None or a.previous_authority is None:p.error('Export requires config and previous authority')
        result=export(a.root,a.reader,a.config,a.previous_authority,a.bundle)
    else:
        if not a.sha256 or a.source_bundle is None or a.config_out is None:p.error('Restore requires SHA, source bundle and config output')
        result=restore(a.root,a.reader,a.bundle,a.sha256,a.source_bundle,a.config_out,resume=a.resume)
    print(json.dumps(result))


if __name__=='__main__':main()
