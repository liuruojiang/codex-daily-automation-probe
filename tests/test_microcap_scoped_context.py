"""Transport counterexamples; synthetic receipts are not market evidence."""
import json
from pathlib import Path
import sys
import zipfile
from types import SimpleNamespace

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import microcap_scoped_context as m


def archive(tmp_path,change=None):
    name=m.RECEIPT;raw=b'{"original_observed_at":"2026-11-09T12:00:00+00:00"}'
    manifest=dict(schema=m.SCHEMA,target='2026-11-09',producer_runtime=m.RUNTIME,
        final_strategy_delivery=False,original_observation_clocks_preserved=True,
        files={name:m.sha(raw)})
    entries={name:raw}
    if change:change(manifest,entries)
    path=tmp_path/'context.zip'
    with zipfile.ZipFile(path,'w') as z:
        for name,raw in entries.items():z.writestr(name,raw)
        z.writestr('manifest.json',m.encode(manifest))
    return path


def test_readback_preserves_original_observation_bytes(tmp_path):
    path=archive(tmp_path)
    manifest,bodies=m.read_context(path,m.sha(path.read_bytes()))
    assert json.loads(bodies[m.RECEIPT])['original_observed_at']=='2026-11-09T12:00:00+00:00'
    assert manifest['final_strategy_delivery'] is False
    assert m.permitted('.microcap_index_cache/st_notice_sources/objects/original-notice.pdf')
    assert not m.permitted('scripts/payload.pdf')


@pytest.mark.parametrize('fault',['sha','bytes','extra','parent','code','nav','clock','runtime'])
def test_context_corruption_and_role_escape_are_rejected(tmp_path,fault):
    def change(manifest,entries):
        if fault=='bytes':entries[m.RECEIPT]=b'changed'
        if fault=='extra':entries['extra.json']=b'{}'
        if fault in ('parent','code','nav'):
            name={'parent':'../escape.json','code':'scripts/payload.py',
                  'nav':'outputs/formal_nav.csv'}[fault]
            entries[name]=b'{}';manifest['files'][name]=m.sha(b'{}')
        if fault=='clock':manifest['original_observation_clocks_preserved']=False
        if fault=='runtime':manifest['producer_runtime']='0'*40
    path=archive(tmp_path,change)
    with pytest.raises(ValueError):m.read_context(path,'0'*64 if fault=='sha' else m.sha(path.read_bytes()))


def test_streamed_restore_verifies_an_object_before_exposing_its_bytes(tmp_path):
    path=archive(tmp_path,lambda manifest,entries:entries.update({m.RECEIPT:b'corrupted original receipt'}))
    _,objects=m.read_context(path,m.sha(path.read_bytes()),lazy=True)
    assert len(objects)==1
    with pytest.raises(ValueError,match='original object SHA differs'):
        next(objects.items())


def test_expansion_limit_is_enforced_before_streaming(tmp_path,monkeypatch):
    path=archive(tmp_path)
    monkeypatch.setattr(m,'MAX_CONTEXT_EXPANDED',1)
    with pytest.raises(ValueError,match='expands beyond limit'):
        m.read_context(path,m.sha(path.read_bytes()),lazy=True)


def test_context_never_writes_a_workspace_with_strategy_state(tmp_path,monkeypatch):
    path=tmp_path/'outputs/microcap_top100_mom16_biweekly_live_v2_0_base_proxy_meta.json'
    path.parent.mkdir();path.write_bytes(b'original strategy state')
    monkeypatch.setattr(m.subprocess,'check_output',lambda *a,**k:m.RUNTIME+'\n')
    with pytest.raises(ValueError,match='strategy-state workspace'):
        m.restore(tmp_path,tmp_path,tmp_path/'absent.zip','0'*64,tmp_path/'source.zip',tmp_path/'config.json')
    assert path.read_bytes()==b'original strategy state'


def test_context_restore_rejects_a_different_producer_before_writes(tmp_path,monkeypatch):
    monkeypatch.setattr(m.subprocess,'check_output',lambda *a,**k:'0'*40+'\n')
    with pytest.raises(ValueError,match='exact producer'):
        m.restore(tmp_path,tmp_path,tmp_path/'absent.zip','0'*64,tmp_path/'source.zip',tmp_path/'config.json')
    assert not list(tmp_path.iterdir())


def test_interrupted_restore_resumes_identical_bytes_and_retains_native_failure(tmp_path,monkeypatch):
    root=tmp_path/'context';root.mkdir()
    original=b'original timestamped metadata receipt'
    target=root/m.RECEIPT;target.parent.mkdir();target.write_bytes(original)
    manifest=dict(target='2026-11-09',source_bundle_sha256='a'*64,source_manifest={},
                  metadata_receipt_sha256=m.sha(original),files={m.RECEIPT:m.sha(original)})
    monkeypatch.setattr(m.subprocess,'check_output',lambda *a,**k:m.RUNTIME+'\n')
    def reject(*args):raise ValueError('Native source proof invalid')
    monkeypatch.setattr(m,'load_reader',lambda *a:SimpleNamespace(checked_producer=reject))
    monkeypatch.setattr(m,'read_context',lambda *a,**k:(manifest,{m.RECEIPT:original}))
    monkeypatch.setattr(m,'read_bundle',lambda *a:({},None,None))
    with pytest.raises(ValueError,match='Native source proof invalid'):
        m.restore(root,root,root/'archive','b'*64,root/'source',root/'config.json',resume=True)
    assert target.read_bytes()==original
    assert not (root/'config.json').exists()
    assert not (root/'maintenance-evidence/context-readback.json').exists()
    target.write_bytes(b'conflicting partial object')
    with pytest.raises(ValueError,match='Existing context object differs'):
        m.restore(root,root,root/'archive','b'*64,root/'source',root/'config.json',resume=True)
    assert target.read_bytes()==b'conflicting partial object'


def test_export_includes_original_predecessor_archive_objects(tmp_path,monkeypatch):
    source=tmp_path/'source';source.mkdir()
    old=['.microcap_index_cache/st_notice_sources/old/archive.json','a'*64]
    new=['.microcap_index_cache/st_notice_sources/new/archive.json','b'*64]
    freshness='.microcap_index_cache/st_notice_sources/freshness.json'
    proof='.microcap_index_cache/st_notice_sources/new/proof.json'
    name=m.NAMES+'2026-11-09.json'
    base='.microcap_index_cache/st_notice_sources/objects/old-scope.json'
    objects={proof:m.encode({'authority':new}),base:b'original predecessor scope',
             name:b'{}',m.POINTER:b'original pointer',freshness:b'original clock',
             '.microcap_index_cache/security_master.csv':b'original master'}
    for path,raw in objects.items():
        dest=source/path;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw)
    receipt=tmp_path/'metadata.json';receipt.write_bytes(m.encode({'files':{}}))
    config=tmp_path/'config.json';config.write_bytes(m.encode(dict(bundle='source.zip',
        bundle_sha256='c'*64,producer_metadata_receipt=str(receipt))))
    specs=[dict(path=proof,sha256=m.sha(objects[proof]))]
    native=SimpleNamespace(checked_producer=lambda *a:({},name),
        original_notice_chain=lambda *a:({'source_files':{proof:m.sha(objects[proof])}},specs))
    monkeypatch.setattr(m,'load_reader',lambda *a:native)
    monkeypatch.setattr(m,'read_bundle',lambda *a:({'target':'2026-11-09'},None,None))
    observed=[]
    def closure(root,*ref):
        observed.append(list(ref))
        return {base:m.sha(objects[base])} if list(ref)==old else {}
    monkeypatch.setitem(sys.modules,'scripts.st_history_scope',SimpleNamespace(source_closure=closure))
    monkeypatch.setitem(sys.modules,'scripts.st_notice_refresh',SimpleNamespace(FRESHNESS=freshness))
    path=tmp_path/'complete.zip'
    result=m.export(source,source,config,old,path)
    with zipfile.ZipFile(path) as packed:
        assert packed.read(name)==objects[name]
    manifest,bodies=m.read_context(path,result['sha256'])
    assert observed==[old,new]
    assert bodies[base]==b'original predecessor scope'
