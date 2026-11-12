"""Actual frozen Native verify_day on software pages; no HTTP or full promotion.

The source-only selector and notice-action adapter run. A Native-boundary error
is explicitly injected at the refresh boundary, not claimed as a full archive.
"""
from datetime import datetime,timezone
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import ModuleType,SimpleNamespace

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import maintain_microcap_local as local

FLOOR=datetime.fromisoformat('2026-11-12T09:00:00+08:00')
TARGET='2026-11-12'
DAYS=['2026-11-11',TARGET]


@pytest.fixture
def case(tmp_path,monkeypatch):
    native=Path(os.environ.get('MICROCAP_NATIVE_ROOT',os.environ.get('MICROCAP_RECOVERY_NATIVE_ROOT',
        r'D:\Codex\workspaces\microcap-quote-fallback-p3-20261112'))).resolve()
    if not (native/'scripts/st_notice_incremental.py').is_file():
        pytest.skip('Actual Native source checkout is required; set MICROCAP_NATIVE_ROOT')
    expected=json.loads((ROOT/'config/microcap_local_producer_runtime.json').read_bytes())['calculation_sources']
    for name in ('scripts/st_notice_incremental.py','scripts/st_history_scope.py'):
        assert local.sha((native/name).read_bytes().replace(b'\r\n',b'\n'))==expected[name]
    package=ModuleType('scripts');package.__path__=[str(native/'scripts')]
    monkeypatch.setitem(sys.modules,'scripts',package)
    modules={}
    for short in ('st_notice_evidence','st_notice_incremental','st_history_scope'):
        name='scripts.'+short;spec=importlib.util.spec_from_file_location(name,native/'scripts'/f'{short}.py')
        module=importlib.util.module_from_spec(spec);monkeypatch.setitem(sys.modules,name,module)
        spec.loader.exec_module(module);modules[short]=module
    monkeypatch.delenv('MICROCAP_ST_NOTICE_ARCHIVE',raising=False)
    monkeypatch.delenv('MICROCAP_ST_NOTICE_ARCHIVE_SHA256',raising=False)
    monkeypatch.setattr(modules['st_notice_incremental'].requests,'get',lambda *_a,**_k:pytest.fail('No HTTP'))
    root=tmp_path/'runtime';out=tmp_path/'selection-receipts';root.mkdir()
    script=root/'scripts/st_notice_incremental.py';script.parent.mkdir();script.write_bytes((native/'scripts/st_notice_incremental.py').read_bytes())
    def put(path,value):
        local.atomic(path,local.encode(value));return local.sha(path.read_bytes())
    def daily(folder,day,stamp):
        folder.mkdir(parents=True,exist_ok=True);increment=modules['st_notice_incremental']
        raw=local.encode(dict(success=1,data=dict(total_hits=0,page_index=1,page_size=100,list=[])))
        local.atomic(folder/'page_0001.json',raw)
        receipt=dict(url=increment.API,params=increment._params(day,1),status=200,sha256=local.sha(raw),
            collected_at=stamp,completed_at=stamp,fixture='explicit software observation')
        digest=put(folder/'page_0001.receipt.json',receipt)
        return put(folder/'manifest.json',dict(schema=increment.SCHEMA,date=day,pages=[dict(
            response='page_0001.json',receipt='page_0001.receipt.json',response_sha256=local.sha(raw),receipt_sha256=digest)]))
    parent=root/local.CACHE/'st_notice_sources'
    installed=parent/'baseline'/'2026-11-11'
    mh=daily(installed,DAYS[0],'2026-11-11T08:00:00+00:00')
    scope=parent/'scope.json';sh=put(scope,dict(end_date='2026-11-10'))
    authority=parent/'baseline-archive.json'
    ah=put(authority,dict(schema_version=3,scope=dict(path=scope.relative_to(root).as_posix(),sha256=sh),
        daily_increments=[dict(date=DAYS[0],manifest=dict(path=(installed/'manifest.json').relative_to(root).as_posix(),sha256=mh))]))
    put(root/modules['st_history_scope'].POINTER_PATH,dict(manifest_path=authority.relative_to(root).as_posix(),manifest_sha256=ah))
    def generation(name='refresh_original',stamp='2026-11-12T07:27:00+00:00',stale=False,partial=False):
        folder=parent/name
        for day in DAYS[:1] if partial else DAYS:
            daily(folder/day,day,'2026-11-11T08:00:00+00:00' if stale and day==DAYS[0] else stamp)
        return folder
    return SimpleNamespace(root=root,out=out,parent=parent,generation=generation,modules=modules)


def snapshot(root):return {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}


def select(case):return local.select_notice_generation(case.root,TARGET,FLOOR,case.out)


def act(case,refresh,**options):
    return local.refresh_notice_with_existing_pages(root=case.root,target=TARGET,required_asof=FLOOR,
        out=case.out,refresh=refresh,**options)


def test_complete_original_pages_reuse_real_native_verifier_and_preserve_original_bytes(case):
    folder=case.generation();before=snapshot(case.root)
    assert select(case)==folder
    assert snapshot(case.root)==before
    receipt=json.loads(next(case.out.glob('*.json')).read_bytes())
    assert receipt['generation']==folder.relative_to(case.root).as_posix()
    assert receipt['required_asof']==FLOOR.isoformat() and receipt['boundary']==DAYS[0]
    assert receipt['original_pages_and_clocks_rewritten'] is False
    assert receipt['native_boundary_gate_required'] is True
    assert all(local.sha((case.root/name).read_bytes())==digest for name,digest in receipt['source_files'].items())
    calls=[]
    assert act(case,lambda **kw:(calls.append(kw) or 'original Native result'))=='original Native result'
    assert calls==[dict(root=case.root,target=TARGET,resume_generation=folder,required_asof=FLOOR)]
    assert len(list(case.out.glob('*.json')))==1 and snapshot(case.root)==before


def test_stale_boundary_day_is_not_admitted_even_if_target_page_is_fresh(case):
    case.generation(stale=True);before=snapshot(case.root)
    assert select(case) is None and not case.out.exists()
    assert snapshot(case.root)==before


def test_partial_generation_is_not_resumed_and_keeps_normal_collection_arguments(case):
    case.generation(partial=True);before=snapshot(case.root);calls=[]
    act(case,lambda **kw:calls.append(kw))
    assert calls==[dict(root=case.root,target=TARGET)]
    assert not case.out.exists() and snapshot(case.root)==before


@pytest.mark.parametrize('marker',['proof.json','archive.json','previous_pointer.json'])
def test_promoted_or_partially_promoted_generation_is_never_admitted(case,marker):
    folder=case.generation();local.atomic(folder/marker,b'explicit software promoted marker')
    before=snapshot(case.root)
    assert select(case) is None and snapshot(case.root)==before


def test_latest_actual_observation_wins_over_directory_name_without_rewriting_any_source(case):
    case.generation('refresh_z_later_name','2026-11-12T07:27:00+00:00')
    latest=case.generation('refresh_a_earlier_name','2026-11-12T08:00:00+00:00')
    before=snapshot(case.root)
    assert select(case)==latest and snapshot(case.root)==before


def test_native_boundary_conflict_is_not_hidden_by_falling_back_to_an_older_candidate(case):
    case.generation('refresh_old','2026-11-12T07:27:00+00:00')
    latest=case.generation('refresh_new','2026-11-12T08:00:00+00:00');before=snapshot(case.root);calls=[]
    def refresh(**kw):
        calls.append(kw)
        raise case.modules['st_notice_evidence'].StNoticeEvidenceError('ST boundary native events changed; exact-hash history review required; pointer preserved')
    with pytest.raises(ValueError,match='exact-hash history review'):act(case,refresh)
    assert [v['resume_generation'] for v in calls]==[latest] and snapshot(case.root)==before


def test_complete_manifest_with_bad_bound_bytes_blocks_instead_of_selecting_an_older_candidate(case):
    case.generation('refresh_old','2026-11-12T07:27:00+00:00')
    latest=case.generation('refresh_new','2026-11-12T08:00:00+00:00')
    local.atomic(latest/TARGET/'page_0001.json',b'changed explicitly invalid software source')
    before=snapshot(case.root)
    with pytest.raises(ValueError,match='response binding differs'):
        act(case,lambda **_kw:pytest.fail('Unknown source cannot start another collection'))
    assert not case.out.exists() and snapshot(case.root)==before


def test_explicit_original_CLI_resume_wins_without_scanning_or_creating_a_selector_receipt(case,monkeypatch):
    explicit=case.parent/'refresh_explicit';calls=[]
    monkeypatch.setattr(local,'select_notice_generation',lambda *_a:pytest.fail('Explicit CLI must have priority'))
    act(case,lambda **kw:calls.append(kw),resume_generation=explicit)
    assert calls==[dict(root=case.root,target=TARGET,resume_generation=explicit,required_asof=FLOOR)]
    assert not case.out.exists()


def test_no_unpromoted_generation_preserves_original_normal_path(case):
    calls=[];act(case,lambda **kw:calls.append(kw))
    assert calls==[dict(root=case.root,target=TARGET)] and not case.out.exists()


def test_newest_complete_generation_with_ambiguous_observation_time_is_not_guessed(case):
    case.generation('refresh_one');case.generation('refresh_two')
    with pytest.raises(ValueError,match='ambiguous'):select(case)
    assert not case.out.exists()


def test_page_path_escape_is_rejected_before_reading_a_foreign_source(case):
    latest=case.generation();path=latest/TARGET/'manifest.json';manifest=json.loads(path.read_bytes())
    manifest['pages'][0]['response']='../outside.json';local.atomic(path,local.encode(manifest))
    with pytest.raises(ValueError,match='page path differs'):select(case)
    assert not case.out.exists()
