"""Private software fixtures; only separate real tests claim Native evidence."""
import hashlib
import json
from pathlib import Path
import sys
import zipfile

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import microcap_notice_prefix as m
from prepare_microcap_local import encode


def put(root,name,raw):
    path=root/name;path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(raw if isinstance(raw,bytes) else encode(raw));return path


def make_notice_case(tmp_path,monkeypatch):
    """Reusable isolated transport/pair fixture; no genuine ST source claims."""
    origin=tmp_path/'origin';origin.mkdir()
    old=dict(schema_version=3,scope={'path':m.PREFIX+'scope.json','sha256':'a'*64},
        legacy_archive=None,daily_increments=[dict(date='2026-11-10',manifest={'path':m.PREFIX+'D10.json','sha256':'b'*64})],symbols={})
    end=dict(old,daily_increments=old['daily_increments']+[dict(date=m.TARGET,manifest={'path':m.PREFIX+'D11.json','sha256':'c'*64})])
    start_path=m.PREFIX+'old/archive.json';end_path=m.PREFIX+'advance/archive.json'
    oldraw=encode(old);endraw=encode(end)
    start=[start_path,hashlib.sha256(oldraw).hexdigest()];finish=[end_path,hashlib.sha256(endraw).hexdigest()]
    p1=m.PREFIX+'advance/proof.json';p2=m.PREFIX+'self/proof.json';endpoint=m.PREFIX+'self/previous_pointer.json'
    first=encode(dict(target=m.TARGET,previous_authority=start,authority=finish,required_asof='2026-11-11T14:32:00+00:00',completed_at='2026-11-11T15:13:00+00:00'))
    second=encode(dict(target=m.TARGET,previous_authority=finish,authority=finish,required_asof='2026-11-12T01:04:00+00:00',completed_at='2026-11-12T02:58:00+00:00'))
    receipts=[dict(path=p1,sha256=hashlib.sha256(first).hexdigest()),dict(path=p2,sha256=hashlib.sha256(second).hexdigest())]
    monkeypatch.setattr(m,'START',start);monkeypatch.setattr(m,'END',finish);monkeypatch.setattr(m,'RECEIPTS',receipts);monkeypatch.setattr(m,'END_POINTER',endpoint)
    bodies={start_path:oldraw,end_path:endraw,p1:first,p2:second,endpoint:encode(dict(manifest_path=finish[0],manifest_sha256=finish[1])),m.PREFIX+'original-http/page.json':b'original software HTTP observation\n'}
    for name,raw in bodies.items():put(origin,name,raw)
    put(origin,m.POINTER,dict(manifest_path=finish[0],manifest_sha256=finish[1]));put(origin,m.FRESHNESS,second)
    files={n:hashlib.sha256(raw).hexdigest() for n,raw in bodies.items()};sizes={n:len(raw) for n,raw in bodies.items()}
    calls=[]
    def native(runtime,data,mode,profile_root=None):
        calls.append((str(runtime),str(data),mode))
        if mode=='prefix':
            for n,h in files.items():
                if hashlib.sha256((Path(data)/n).read_bytes()).hexdigest()!=h:raise m.NoticePrefixError('software original closure changed')
            return dict(mode='prefix',native_runtime=m.PRODUCER,previous_authority=start,authority=finish,target=m.TARGET,
                receipt_count=2,old_prefix_unchanged=True,original_clocks_retained=True,files=files,sizes=sizes)
        pointer=json.loads((Path(data)/m.POINTER).read_bytes());ref=[pointer['manifest_path'],pointer['manifest_sha256']]
        if ref not in (start,finish):raise m.NoticePrefixError('software unknown authority')
        if ref==finish:
            proof=json.loads((Path(data)/m.FRESHNESS).read_bytes())
            if proof['authority']!=finish:raise m.NoticePrefixError('software mismatched pointer/freshness')
        return dict(mode='current',native_runtime=m.PRODUCER,state='advance' if ref==start else 'covered',authority=ref,
            old_prefix_unchanged=True,original_clocks_retained=True)
    monkeypatch.setattr(m,'runtime_role',lambda root,expected:Path(root).resolve())
    monkeypatch.setattr(m,'validate_runtime',lambda root,protected:None)
    monkeypatch.setattr(m,'load_pool',lambda pool,date:({},[f'{i:06}' for i in range(300)],'fixture'))
    bundle=tmp_path/'notice.zip';m.export_prefix(root=origin,bundle=bundle,issuer_head='d'*40,native=native)
    private=tmp_path/'private';root=private/'state/runtime';protected=private/'consumer';root.mkdir(parents=True);protected.mkdir()
    put(root,start_path,oldraw);put(root,m.POINTER,dict(manifest_path=start[0],manifest_sha256=start[1]))
    put(root,m.FRESHNESS,dict(target='2026-11-10',authority=start))
    refs={put(root.parent,'bootstrap.json',b'original software bootstrap\n'):b'original software bootstrap\n',
          put(root.parent,'reference-proxy.csv',b'original software reference\n'):b'original software reference\n',
          put(protected,'outputs/proxy.csv',b'original protected anchor\n'):b'original protected anchor\n'}
    monkeypatch.setattr(m,'checked_bootstrap',lambda *a:dict(refs))
    args=dict(root=root,protected_root=protected,state=root.parent,pool=tmp_path/'pool.json',bundle=bundle,
        expected_sha=m.sha_file(bundle),not_after='2026-11-12',out=private/'intake',native=native)
    return dict(origin=origin,root=root,protected=protected,files=files,bodies=bodies,bundle=bundle,args=args,native=native,calls=calls,refs=refs)


@pytest.fixture
def notice_case(tmp_path,monkeypatch):return make_notice_case(tmp_path,monkeypatch)


def test_both_original_receipts_are_independent_of_mutable_current_pointer(notice_case):
    c=notice_case;value=m.inspect_bundle(bundle=c['bundle'],expected_sha=m.sha_file(c['bundle']),not_after='2026-11-12')
    assert value['receipts']==m.RECEIPTS and value['active_freshness_spec']==m.RECEIPTS[1]
    assert m.POINTER not in value['files'] and m.FRESHNESS not in value['files']
    assert value['source_inputs_only'] and value['final_strategy_delivery'] is False


def test_private_import_advances_pair_and_keeps_protected_consumer_and_source_originals(notice_case):
    c=notice_case;source={n:(c['origin']/n).read_bytes() for n in c['files']}
    result=m.import_prefix(**c['args'])
    assert result['status']=='approved_notice_prefix_imported_source_only'
    assert result['target']==m.TARGET and result['current_signal_ready'] is False
    assert (c['root']/m.FRESHNESS).read_bytes()==c['bodies'][m.RECEIPTS[1]['path']]
    assert json.loads((c['root']/m.FRESHNESS).read_bytes())['target']=='2026-11-11'
    assert all(p.read_bytes()==raw for p,raw in c['refs'].items())
    assert all((c['origin']/n).read_bytes()==raw for n,raw in source.items())
    assert not (c['protected']/m.POINTER).exists()


def test_same_authority_never_replaces_a_later_freshness_clock(notice_case):
    c=notice_case;m.import_prefix(**c['args'])
    later=encode(dict(target=m.TARGET,authority=m.END,previous_authority=m.END,required_asof='2026-11-13T01:05:00+00:00'))
    put(c['root'],m.FRESHNESS,later)
    result=m.import_prefix(**c['args'])
    assert result['status']=='skipped_notice_prefix_already_covered' and (c['root']/m.FRESHNESS).read_bytes()==later


def test_second_pair_write_failure_rolls_back_and_can_resume_same_transaction(notice_case,monkeypatch):
    c=notice_case;old=m._pair(c['root']);write=m._write_pair_member;failed=[]
    def fail_once(path,raw):
        if Path(path)==c['root']/m.FRESHNESS and raw!=old[m.FRESHNESS] and not failed:
            failed.append(True);raise OSError('software crash before freshness replacement')
        return write(path,raw)
    monkeypatch.setattr(m,'_write_pair_member',fail_once)
    with pytest.raises(OSError):m.import_prefix(**c['args'])
    assert m._pair(c['root'])==old
    assert json.loads((c['args']['out']/'transaction.json').read_bytes())['status']=='rolled_back'
    result=m.import_prefix(**c['args'])
    assert result['status']=='approved_notice_prefix_imported_source_only'


def test_newer_valid_authority_is_read_only(notice_case):
    c=notice_case;newref=[m.PREFIX+'future/archive.json','f'*64]
    old_native=c['native']
    def newer(runtime,data,mode,profile_root=None):
        if mode=='prefix':return old_native(runtime,data,mode,profile_root)
        return dict(mode='current',state='covered',authority=newref,native_runtime=m.PRODUCER,
                    old_prefix_unchanged=True,original_clocks_retained=True)
    old=m._pair(c['root']);result=m.import_prefix(**dict(c['args'],native=newer))
    assert result['authority']==newref and m._pair(c['root'])==old


@pytest.mark.parametrize('fault',['profile','runtime','receipt','global_pointer','clock_body','unbound'])
def test_rehashed_wrong_profile_or_open_payload_never_changes_private_pair(notice_case,fault):
    c=notice_case
    with zipfile.ZipFile(c['bundle']) as z:bodies={n:z.read(n) for n in z.namelist()}
    manifest=json.loads(bodies['manifest.json'])
    if fault=='profile':manifest['profile']='unreviewed'
    elif fault=='runtime':manifest['producer_runtime']='a'*40
    elif fault=='receipt':manifest['receipts']=manifest['receipts'][:1]
    elif fault=='global_pointer':
        bodies[m.POINTER]=b'{}';manifest['files'][m.POINTER]=hashlib.sha256(b'{}').hexdigest();manifest['sizes'][m.POINTER]=2
    elif fault=='clock_body':bodies[m.RECEIPTS[1]['path']]+=b'changed'
    else:bodies['unbound.json']=b'{}'
    bodies['manifest.json']=encode(manifest);bad=c['bundle'].with_name('bad.zip')
    with zipfile.ZipFile(bad,'w') as z:
        for name,raw in bodies.items():z.writestr(name,raw)
    old=m._pair(c['root'])
    with pytest.raises((m.NoticePrefixError,RuntimeError)):
        m.import_prefix(**dict(c['args'],bundle=bad,expected_sha=m.sha_file(bad)))
    assert m._pair(c['root'])==old


def test_unknown_authority_does_not_adopt_even_an_authentic_payload(notice_case):
    c=notice_case;put(c['root'],m.POINTER,dict(manifest_path=m.PREFIX+'other.json',manifest_sha256='e'*64));old=m._pair(c['root'])
    with pytest.raises(m.NoticePrefixError,match='unknown'):m.import_prefix(**c['args'])
    assert m._pair(c['root'])==old


def test_immutable_conflict_never_promotes_pair(notice_case):
    c=notice_case;put(c['root'],m.RECEIPTS[0]['path'],b'conflicting original');old=m._pair(c['root'])
    with pytest.raises(m.NoticePrefixError,match='immutable'):m.import_prefix(**c['args'])
    assert m._pair(c['root'])==old


def test_prefix_archive_rejects_any_old_field_or_daily_rewrite():
    old=dict(schema_version=3,daily_increments=[dict(date='2026-11-10')],scope={'a':1})
    assert m.prefix_archive(old,dict(old,daily_increments=old['daily_increments']+[dict(date=m.TARGET)]))
    assert not m.prefix_archive(old,dict(old,scope={'a':2}))
    assert not m.prefix_archive(old,dict(old,daily_increments=[dict(date='2026-11-09')]))


def test_declared_old_baseline_is_hash_bound_borrowed_without_copy_or_rewrite(notice_case,tmp_path):
    c=notice_case;name=m.PREFIX+'original-http/page.json';raw=c['bodies'][name]
    put(c['root'],name,raw);calls=[]
    def native(runtime,data,mode,profile_root=None):
        result=c['native'](runtime,data,mode,profile_root)
        if mode=='prefix':
            result=dict(result,baseline_files={name:c['files'][name]},proof_files={})
            if Path(data)!=c['origin']:
                assert (Path(data)/name).samefile(c['root']/name)
                calls.append('read_only_baseline_hardlink')
        return result
    bundle=tmp_path/'delta.zip';m.export_prefix(root=c['origin'],bundle=bundle,issuer_head='d'*40,native=native)
    value=m.inspect_bundle(bundle=bundle,expected_sha=m.sha_file(bundle),not_after='2026-11-12')
    with zipfile.ZipFile(bundle) as z:assert name not in z.namelist()
    assert value['baseline_files']=={name:c['files'][name]}
    result=m.import_prefix(**dict(c['args'],bundle=bundle,expected_sha=m.sha_file(bundle),native=native))
    assert result['status']=='approved_notice_prefix_imported_source_only'
    assert calls==['read_only_baseline_hardlink'] and (c['root']/name).read_bytes()==raw
    assert not list(c['args']['out'].glob('notice-view-*'))


def test_existing_same_sha_does_not_authorize_moving_new_dependencies_to_baseline(notice_case,tmp_path):
    c=notice_case;name=m.PREFIX+'original-http/page.json';put(c['root'],name,c['bodies'][name])
    with zipfile.ZipFile(c['bundle']) as z:bodies={n:z.read(n) for n in z.namelist()}
    value=json.loads(bodies['manifest.json']);value['baseline_files'][name]=value['payload_files'].pop(name)
    del bodies[name];bodies['manifest.json']=encode(value)
    bad=tmp_path/'unreviewed-borrow.zip'
    with zipfile.ZipFile(bad,'w') as z:
        for n,raw in bodies.items():z.writestr(n,raw)
    old=m._pair(c['root'])
    with pytest.raises(m.NoticePrefixError,match='native_and_transport'):
        m.import_prefix(**dict(c['args'],bundle=bad,expected_sha=m.sha_file(bad)))
    assert m._pair(c['root'])==old
