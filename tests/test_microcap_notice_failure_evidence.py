"""Offline diagnostic retention fixtures, not certified strategy/market data."""
import hashlib
import json
from pathlib import Path
import sys
from datetime import datetime, timezone

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import collect_microcap_notice_failure as evidence

OLD='refresh_20261104T142445698750Z_58168aa31417'
NEW='refresh_20261105T082445698750Z_58168aa31418'
def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw=json.dumps(value).encode();path.write_bytes(raw);return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def case(tmp_path,monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            return cls(2026,11,5,10,tzinfo=timezone.utc)
    monkeypatch.setattr(evidence,'datetime',Clock)
    root=tmp_path/'strategy';root.mkdir();out=tmp_path/'diagnostic'
    folder=root/evidence.PARENT/OLD/'2026-11-04'
    response=dump(folder/'page_0001.json',{'synthetic': 'old boundary'})
    receipt=dump(folder/'page_0001.receipt.json',{'status':200,'sha256':response})
    mhash=dump(folder/'manifest.json',dict(date='2026-11-04',pages=[dict(response='page_0001.json',receipt='page_0001.receipt.json',response_sha256=response,receipt_sha256=receipt)]))
    archive=root/evidence.PARENT/OLD/'archive.json'
    ahash=dump(archive,dict(daily_increments=[dict(date='2026-11-04',manifest=dict(path=(folder/'manifest.json').relative_to(root).as_posix(),sha256=mhash))]))
    dump(root/evidence.POINTER,dict(manifest_path=archive.relative_to(root).as_posix(),manifest_sha256=ahash))
    return root,out,out/'baseline.json'


def snapshot(case):
    result=evidence.snapshot(*case,'run-1')
    # Frozen synthetic run boundary: test filesystem generations are historical fixtures.
    baseline=case[2]
    if baseline.exists():
        x=json.loads(baseline.read_text());x['started_at']='2026-11-05T08:00:00+00:00';baseline.write_text(json.dumps(x))
    return result
def collect(case):return evidence.collect(*case,'run-1')
def source_hashes(root):return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}


def test_failed_generation_retains_old_new_and_truncated_exact_bytes(case):
    root,out,_=case
    # Unrelated historical evidence must never be bundled.
    dump(root/evidence.PARENT/OLD/'2018-01-01'/'page_9999.json',{'history':'not selected'})
    assert snapshot(case)['complete_capture']
    folder=root/evidence.PARENT/NEW/'2026-11-04'
    dump(folder/'page_0001.receipt.json',{'error':'StNoticeEvidenceError'})
    (folder/'page_0001.json').write_bytes(b'{"data":truncated')
    dump(folder/'invalid_manifest.json',{'partial':True})
    before=source_hashes(root)
    result=collect(case)
    assert result['complete_capture'] and result['new_generations']==[NEW]
    assert source_hashes(root)==before
    assert len(result['files'])==3
    assert any(f.get('json_error') for f in result['files'])
    for f in result['files']:
        assert (out/f['artifact']).read_bytes()==(root/f['source']).read_bytes()
        assert hashlib.sha256((out/f['artifact']).read_bytes()).hexdigest()==f['sha256']
    assert not list(out.rglob('page_9999.json'))
    assert len(list((out/'old_boundary').rglob('page_0001*')))==2


def test_no_new_generation_does_not_upload_whole_history(case):
    snapshot(case);result=collect(case)
    assert result['complete_capture'] and result['files']==[] and result['new_generations']==[]


def test_exact_static_identity_snapshots_survive_failure_without_mutation(case):
    root,out,_=case
    snapshot(case)
    for name in ('active_universe.csv','current_st.csv'):
        (root/'.microcap_index_cache'/name).write_bytes(b'code,name\n000001,ST synthetic\n')
    before=source_hashes(root)
    result=collect(case)
    assert result['complete_capture'] and len(result['files'])==2
    assert source_hashes(root)==before
    for entry in result['files']:
        assert entry['artifact'].startswith('static_snapshots/')
        assert 'json_error' not in entry
        assert (out/entry['artifact']).read_bytes()==(root/entry['source']).read_bytes()


def test_missing_source_and_missing_baseline_leave_diagnostic_summary(tmp_path):
    root=tmp_path/'missing';out=tmp_path/'out';baseline=out/'baseline.json'
    result=evidence.snapshot(root,out,baseline,'run-1')
    assert not result['complete_capture'] and baseline.exists() and not root.exists()
    assert evidence.collect(root,out,baseline,'run-1')['complete_capture']
    baseline.unlink()
    result=evidence.collect(root,out,baseline,'run-1')
    assert not result['complete_capture'] and not result['files']
    assert json.loads((out/'collection_summary.json').read_text())['errors']


@pytest.mark.parametrize('path',['../outside.json','/outside.json','C:/secret.json','foo\\secret.json','.microcap_index_cache/st_notice_sources/../secret.json'])
def test_pointer_escape_never_copies_outside_scope(case,path):
    root,out,_=case;dump(root/evidence.POINTER,dict(manifest_path=path,manifest_sha256='0'*64))
    result=snapshot(case)
    assert not result['complete_capture']
    assert all(f['source']==evidence.POINTER for f in result['files'])


def test_malformed_old_pointer_still_collects_current_failed_generation(case):
    root,_,_=case;(root/evidence.POINTER).write_bytes(b'{')
    assert not snapshot(case)['complete_capture']
    dump(root/evidence.PARENT/NEW/'2026-11-05'/'page_0001.json',{'failed':True})
    assert len(collect(case)['files'])==1


@pytest.mark.parametrize('change',['run','root','generation_list'])
def test_wrong_run_or_tampered_baseline_never_collects_old_history(case,change):
    snapshot(case);root,_,baseline=case;x=json.loads(baseline.read_text())
    if change=='run':x['run_id']='another-run'
    if change=='root':x['root']=str(root.parent)
    if change=='generation_list':x['existing']=['../escape']
    baseline.write_text(json.dumps(x))
    result=collect(case);assert not result['complete_capture'] and not result['files']


def test_oversize_body_keeps_summary_and_other_small_receipt(case,monkeypatch):
    snapshot(case);root,_,_=case;folder=root/evidence.PARENT/NEW/'2026-11-05'
    dump(folder/'page_0001.receipt.json',{'error':'bad body'})
    (folder/'page_0001.json').write_bytes(b'x'*1000)
    monkeypatch.setattr(evidence,'MAX_FILE_BYTES',100)
    result=collect(case)
    assert not result['complete_capture'] and len(result['files'])==1
    assert result['files'][0]['source'].endswith('.receipt.json')


def test_generation_cap_never_turns_into_full_history_capture(case,monkeypatch):
    snapshot(case);root,_,_=case;dump(root/evidence.PARENT/NEW/'2026-11-05'/'page_0001.json',{})
    monkeypatch.setattr(evidence,'MAX_GENERATIONS',0)
    result=collect(case);assert not result['complete_capture'] and not result['files']


@pytest.mark.parametrize('time',['2026-11-06T00:00:00+00:00','2026-11-05T08:00:00'])
def test_future_or_naive_baseline_cannot_claim_generation(case,time):
    snapshot(case);root,_,baseline=case;x=json.loads(baseline.read_text());x['started_at']=time;baseline.write_text(json.dumps(x))
    dump(root/evidence.PARENT/NEW/'2026-11-05'/'page_0001.json',{})
    result=collect(case);assert not result['complete_capture'] and not result['files']


def test_removing_old_generation_from_baseline_does_not_upload_it(case):
    snapshot(case);baseline=case[2];x=json.loads(baseline.read_text());x['existing']=[];baseline.write_text(json.dumps(x))
    result=collect(case);assert not result['complete_capture'] and not result['files']


def test_output_or_baseline_cannot_modify_production(case):
    root,out,_=case;before=source_hashes(root)
    with pytest.raises(ValueError):evidence.snapshot(root,root/'bad',root/'bad/baseline.json','run-1')
    with pytest.raises(ValueError):evidence.snapshot(root,out,root/'baseline.json','run-1')
    assert before==source_hashes(root)


def test_linked_generation_is_refused(case,tmp_path):
    snapshot(case);root,_,_=case;external=tmp_path/'outside';external.mkdir()
    (external/'secret.json').write_text('do not read')
    link=root/evidence.PARENT/NEW
    # Windows developer mode/admin and Linux CI support directory links; no skip.
    link.symlink_to(external,target_is_directory=True)
    result=collect(case)
    assert not result['complete_capture'] and not result['files']


@pytest.mark.parametrize('name',['baseline.json','snapshot_summary.json'])
def test_linked_diagnostic_metadata_cannot_overwrite_source(case,name):
    root,out,_=case;out.mkdir();target=root/evidence.POINTER;before=target.read_bytes()
    (out/name).symlink_to(target)
    with pytest.raises(ValueError):snapshot(case)
    assert target.read_bytes()==before


def test_workflow_failure_upload_is_bounded_and_does_not_change_delivery_gate():
    jobs=yaml.safe_load((ROOT/'.github/workflows/microcap-realtime-digest.yml').read_text())['jobs']
    steps=jobs['send']['steps'];by={x.get('id'):x for x in steps}
    snapshot_step=by['notice_snapshot'];refresh=by['refresh_state'];collect_step=by['notice_evidence']
    assert snapshot_step['if']==refresh['if']
    assert snapshot_step['continue-on-error'] is True and collect_step['continue-on-error'] is True
    assert steps.index(snapshot_step)<steps.index(refresh)<steps.index(collect_step)
    assert 'always()' in collect_step['if'] and "steps.refresh_state.outcome != 'skipped'" in collect_step['if']
    upload=next(x for x in steps if x.get('name')=='Upload bounded notice diagnostics')
    assert upload['if']==collect_step['if'] and upload['with']['include-hidden-files'] is True
    assert upload['with']['path']=='${{ runner.temp }}/microcap-notice-run-evidence/'
    assert 'st_notice_sources' not in upload['with']['path']
    assert all('notice_' not in x.get('if','') for x in steps if x.get('id') in ('send_gmail','smtp_receipt'))
    assert 'StNoticeEvidenceError' in refresh['run'] and 'Non-retryable lineage' in refresh['run']
