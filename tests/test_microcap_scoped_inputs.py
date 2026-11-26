import json
from pathlib import Path
import sys
import zipfile
from types import SimpleNamespace

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import microcap_scoped_inputs as package


def test_immutable_reader_and_producer_contract_are_both_required(tmp_path, monkeypatch):
    calls=[]
    pin=next(iter(package.REVIEWED_CONSUMERS))
    def verify(root, commit):
        calls.append(('release',commit))
    def contract(root, manifest):
        calls.append(('sources',manifest))
        if manifest.get('changed'):
            raise ValueError('Unreviewed source change')
    monkeypatch.setitem(sys.modules,'scripts.top100_delivery',SimpleNamespace(verify_candidate_release=verify))
    monkeypatch.setitem(sys.modules,'scripts.scoped_source_consumer',SimpleNamespace(validate_source_contract=contract))
    package.validate_consumer(tmp_path,{},pin)
    assert calls==[('release',pin),('sources',{})]
    with pytest.raises(ValueError,match='Unreviewed source change'):
        package.validate_consumer(tmp_path,{'changed':True},pin)
    with pytest.raises(ValueError,match='exact reviewed'):
        package.validate_consumer(tmp_path,{},'0'*40)


def test_modified_reader_is_rejected_before_source_adoption(tmp_path,monkeypatch):
    def reject(*args):raise ValueError('Executable code differs from candidate commit')
    monkeypatch.setitem(sys.modules,'scripts.top100_delivery',SimpleNamespace(verify_candidate_release=reject))
    monkeypatch.setitem(sys.modules,'scripts.scoped_source_consumer',SimpleNamespace(validate_source_contract=lambda *a:pytest.fail('Release must pass first')))
    with pytest.raises(ValueError,match='Executable code differs'):
        package.validate_consumer(tmp_path,{},next(iter(package.REVIEWED_CONSUMERS)))


def archive(tmp_path, change=None):
    raw=b'date,close_raw\n2026-11-06,10\n'
    name=package.CACHE+'prices_raw/000001.csv'
    master=b'symbol,list_date,delist_date,exchange,board\n000001,1991-04-03,,SZSE,main\n'
    manifest=dict(schema=package.SCHEMA,runtime_sha=package.RUNTIME,target='2026-11-06',
        source_inputs_only=True,native_ST_refresh_required_at_consumer=True,final_strategy_delivery=False,
        symbols=[f'{n:06d}' for n in range(1,301)],files={name:package.sha(raw)},
        evidence={'maintenance-evidence/master.csv':package.sha(master)},master_sha256=package.sha(master))
    entries={name:raw,'maintenance-evidence/master.csv':master}
    if change:change(manifest,entries)
    path=tmp_path/'sources.zip'
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
        for key,value in entries.items():z.writestr(key,value)
        z.writestr('manifest.json',package.encode(manifest))
    return path


def test_transport_decode_does_not_claim_native_acceptance(tmp_path):
    path=archive(tmp_path)
    m,b,d=package.read_bundle(path,'2026-11-06',package.sha(path.read_bytes()))
    assert m['native_ST_refresh_required_at_consumer'] is True
    assert m['final_strategy_delivery'] is False


def test_original_native_raw_responses_are_retained_by_transport(tmp_path):
    assert package.permitted(package.CACHE+'share_sources/providers/type/000001/id/sina.raw')
    assert package.permitted(package.CACHE+'trade_status/raw/000001.json')
    assert not package.permitted(package.CACHE+'share_sources/locks/000001.lock')
    assert not package.permitted(package.CACHE+'share_sources/payload.py')
    assert package.permitted(package.CACHE+'share_sources/baselines/'+'a'*64+'/accepted_issuer_evidence.bin')
    assert not package.permitted(package.CACHE+'share_sources/baselines/'+'a'*64+'/other.bin')
    assert not package.permitted(package.CACHE+'prices_raw/000001.bin')


@pytest.mark.parametrize('kind',['parent','absolute','code','other_issuer','hash','extra','future','runtime','partial'])
def test_invalid_transport_cannot_be_installed(tmp_path,kind):
    def change(m,b):
        if kind in ('parent','absolute','code','other_issuer'):
            name={'parent':'../private.json','absolute':'/outside.json','code':'scripts/payload.py',
                  'other_issuer':package.CACHE+'prices_raw/999999.csv'}[kind]
            b[name]=b'data';m['files'][name]=package.sha(b'data')
        if kind=='hash': b[next(iter(m['files']))]=b'changed'
        if kind=='extra': b['unlisted.json']=b'{}'
        if kind=='future':m['target']='2026-11-09'
        if kind=='runtime':m['runtime_sha']='0'*40
        if kind=='partial':m['symbols']=m['symbols'][:299]
    with pytest.raises(ValueError):package.read_bundle(archive(tmp_path,change),'2026-11-06')


@pytest.mark.parametrize('kind',['price_rewrite','price_delete','share_rewrite'])
def test_history_cannot_be_rewritten_on_import(kind):
    if kind.startswith('price'):
        before=b'date,close_raw\n2026-11-05,10\n'
        after=b'date,close_raw\n2026-11-06,11\n' if kind=='price_delete' else b'date,close_raw\n2026-11-05,10.01\n'
        with pytest.raises(ValueError):package.preserve_history(before,after,'date','close_raw','2026-11-05')
    else:
        with pytest.raises(ValueError):package.preserve_history(
            b'change_date,total_shares_10k\n2020-01-01,100\n',
            b'change_date,total_shares_10k\n2020-01-01,100\n2026-11-04,101\n',
            'change_date','total_shares_10k','2026-11-05')


def test_new_tail_is_allowed_after_certified_cutoff():
    package.preserve_history(b'change_date,total_shares_10k\n2020-01-01,100\n',
        b'change_date,total_shares_10k\n2020-01-01,100.0\n2026-11-06,101\n',
        'change_date','total_shares_10k','2026-11-05')


def test_native_rejection_rolls_back_existing_inputs_and_preserves_evidence(tmp_path):
    name=package.CACHE+'prices_raw/000001.csv';path=package.bound(tmp_path,name)
    path.parent.mkdir(parents=True);path.write_bytes(b'original approved bytes')
    manifest={'target':'2026-11-06','files':{name:package.sha(b'candidate bytes')}}
    def reject():raise ValueError('native source check rejected candidate')
    with pytest.raises(ValueError,match='native source check'):
        package.apply_inputs(tmp_path,manifest,{name:b'candidate bytes'},reject)
    assert path.read_bytes()==b'original approved bytes'
    receipts=list((tmp_path/'.microcap_index_cache/local_input_imports').glob('*/receipt.json'))
    assert json.loads(receipts[0].read_bytes())['status']=='rolled_back_existing_inputs'


def test_unfinished_maintenance_cannot_be_exported(tmp_path):
    with pytest.raises(ValueError,match='complete hash-verified'):
        package.export(tmp_path/'runtime',tmp_path,'2026-11-06',tmp_path/'out.zip')
    assert not (tmp_path/'out.zip').exists()


def test_source_prewarm_keeps_consumed_pointers_unchanged(tmp_path):
    name=package.CACHE+'share_sources/000001.json'
    original=package.bound(tmp_path,name);original.parent.mkdir(parents=True)
    original.write_bytes(b'old authenticated source pointer')
    raw=b'new independently verified source pointer'
    manifest=dict(target='2026-11-06',files={name:package.sha(raw)})
    calls=[]
    def verify(candidate):
        assert original.read_bytes()==b'old authenticated source pointer'
        assert package.bound(candidate,name).read_bytes()==raw
        calls.append(candidate)
    receipt=package.stage_inputs(tmp_path,manifest,{name:raw},'a'*64,{},verify)
    assert calls and original.read_bytes()==b'old authenticated source pointer'
    value=json.loads(receipt.read_bytes())
    assert value['production_pointers_changed'] is False
    assert value['native_late_adoption_required'] is True


def test_failed_staging_preserves_originals_and_does_not_issue_success(tmp_path):
    name=package.CACHE+'prices_raw/000001.csv'
    original=package.bound(tmp_path,name);original.parent.mkdir(parents=True)
    original.write_bytes(b'old approved raw')
    manifest=dict(target='2026-11-06',files={name:package.sha(b'bad candidate')})
    def reject(candidate):raise ValueError('native rejection')
    with pytest.raises(ValueError,match='native rejection'):
        package.stage_inputs(tmp_path,manifest,{name:b'bad candidate'},'b'*64,{},reject)
    assert original.read_bytes()==b'old approved raw'
    assert not package.bound(tmp_path,package.CACHE+'local_input_imports/'+'b'*64+'/receipt.json').exists()


@pytest.mark.parametrize('fault',['runtime','code'])
def test_completed_receipt_cannot_be_relabelled_after_native_code_change(tmp_path,monkeypatch,fault):
    directory=tmp_path/'runs/2026-11-06';directory.mkdir(parents=True)
    root=tmp_path/'runtime';root.mkdir();(root/'native.py').write_bytes(b'changed code')
    context=dict(runtime_sha=package.RUNTIME,native_sources={'native.py':package.sha(b'original code')})
    if fault=='runtime':context['runtime_sha']='0'*40
    (directory/'result.json').write_bytes(package.encode({'context':context}))
    monkeypatch.setattr(package,'completed',lambda *a,**k:True)
    with pytest.raises(ValueError,match='another native runtime|code changed'):
        package.export(root,tmp_path,'2026-11-06',tmp_path/'output.zip')
    assert not (tmp_path/'output.zip').exists()
