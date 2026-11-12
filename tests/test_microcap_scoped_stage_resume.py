"""Fault recovery invariants for scoped native-stage receipts; no markets/mail."""
import json
from pathlib import Path
import shutil
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from microcap_scoped_stage_resume import StageResume, formal_objects
from prepare_microcap_local import encode,sha


def fixture(root):
    out=root/'delivery';consumer=root/'consumer'
    out.mkdir(parents=True);consumer.mkdir(parents=True)
    data=consumer/'data.csv';calls=[]
    def run(label,argv):
        calls.append(label)
        logs=out/'commands'/label;logs.mkdir(parents=True,exist_ok=True)
        if label=='refresh':data.write_bytes(b'date,value\n2026-11-13,101\n')
        result={key:logs/name for key,name in (('stdout','stdout.log'),('stderr','stderr.log'),('process','process.json'))}
        result['stdout'].write_bytes(b'success\n');result['stderr'].write_bytes(b'')
        result['process'].write_bytes(encode(dict(exit_code=0,command=list(map(str,argv)))))
        return result
    context=dict(target='2026-11-13',producer='exact-source',parent='original-parent')
    return out,consumer,data,calls,run,context


def test_check_failure_restart_reuses_successful_refresh_without_running_it_twice(tmp_path):
    out,consumer,data,calls,run,context=fixture(tmp_path)
    stage=StageResume(out,consumer,context,run)
    stage.call('refresh',['refresh',consumer,out],[data])
    def fail(label,argv):
        if label=='check':raise RuntimeError('isolated independent check unavailable')
        return run(label,argv)
    with pytest.raises(RuntimeError):StageResume(out,consumer,context,fail).call('check',['check',consumer],[data])
    resumed=StageResume(out,consumer,context,run)
    assert resumed.call('refresh',['refresh',consumer,out],[data])['stage_reused'] is True
    resumed.call('check',['check',consumer],[data])
    assert calls==['refresh','check']
    assert (out/'stage-receipts/refresh.json').is_file() and (out/'stage-receipts/check.json').is_file()


@pytest.mark.parametrize('fault',['output','stdout','context','path_escape'])
def test_changed_evidence_never_becomes_an_implicit_repeat_or_success(tmp_path,fault):
    out,consumer,data,calls,run,context=fixture(tmp_path)
    stage=StageResume(out,consumer,context,run)
    stage.call('refresh',['refresh',consumer,out],[data])
    if fault=='output':data.write_bytes(b'changed economic rows')
    elif fault=='stdout':(out/'commands/refresh/stdout.log').write_bytes(b'changed evidence')
    elif fault=='context':context=dict(context,parent='another original parent')
    else:
        path=out/'stage-receipts/refresh.json';receipt=json.loads(path.read_bytes())
        receipt['objects'][0]['path']='../../outside';path.write_bytes(encode(receipt))
    with pytest.raises(ValueError):StageResume(out,consumer,context,run).call('refresh',['refresh',consumer,out],[data])
    assert calls==['refresh']


def test_exact_closed_stage_can_be_read_after_private_root_relocation(tmp_path):
    out,consumer,data,calls,run,context=fixture(tmp_path/'original')
    StageResume(out,consumer,context,run).call('refresh',['refresh',consumer,out],[data])
    fresh=tmp_path/'next-run';fresh.mkdir()
    new_out,new_consumer=fresh/'delivery',fresh/'consumer'
    shutil.copytree(out,new_out);shutil.copytree(consumer,new_consumer)
    def forbidden(*args):raise AssertionError('Successful native producer was repeated')
    result=StageResume(new_out,new_consumer,context,forbidden).call('refresh',['refresh',new_consumer,new_out],[new_consumer/'data.csv'])
    assert result['stage_reused'] is True
    assert all(p.is_relative_to(new_out) for k,p in result.items() if k!='stage_reused')


def test_unknown_process_completion_cannot_issue_reusable_success_receipt(tmp_path):
    out,consumer,data,calls,run,context=fixture(tmp_path)
    def unknown(label,argv):
        result=run(label,argv)
        result['process'].write_bytes(encode(dict(exit_code=None,completion_unconfirmed=True)))
        return result
    with pytest.raises(ValueError,match='Unknown or unsuccessful'):
        StageResume(out,consumer,context,unknown).call('refresh',['refresh',consumer],[data])
    assert not (out/'stage-receipts/refresh.json').exists()


def test_native_normalized_manifest_accepts_windows_text_but_receipt_retains_original_bytes(tmp_path):
    out,consumer,data,calls,run,context=fixture(tmp_path)
    raw=b'date,value\r\n2026-11-13,101\r\n';data.write_bytes(raw)
    (consumer/'outputs').mkdir()
    manifest=consumer/'outputs/top100_delivery_manifest.json'
    manifest.write_bytes(encode(dict(ok=True,status='complete',scope='whole_workspace_delivery',
        expected_date='2026-11-13',inputs={'data.csv':sha(raw.replace(b'\r\n',b'\n'))},artifacts={})))
    paths=formal_objects(consumer,'2026-11-13')
    assert data in paths and data.read_bytes()==raw
    stage=StageResume(out,consumer,context,run)
    stage.call('check',['check',consumer],paths)
    record=json.loads((out/'stage-receipts/check.json').read_bytes())
    item=next(row for row in record['objects'] if row['path']=='data.csv')
    assert item['sha256']==sha(raw)
    data.write_bytes(raw.replace(b'\r\n',b'\n'))
    with pytest.raises(ValueError,match='output changed'):
        stage.call('check',['check',consumer],paths)


def test_original_st_notice_evidence_is_never_line_ending_normalized(tmp_path):
    root=tmp_path/'consumer';(root/'outputs').mkdir(parents=True)
    evidence=root/'data/st_notice_evidence/original.raw';evidence.parent.mkdir(parents=True)
    raw=b'original issuer\r\n';evidence.write_bytes(raw)
    manifest=root/'outputs/top100_delivery_manifest.json'
    def write(digest):
        manifest.write_bytes(encode(dict(ok=True,status='complete',scope='whole_workspace_delivery',
            expected_date='2026-11-13',inputs={'data/st_notice_evidence/original.raw':digest},artifacts={})))
    write(sha(raw));assert evidence in formal_objects(root,'2026-11-13')
    write(sha(raw.replace(b'\r\n',b'\n')))
    with pytest.raises(ValueError,match='closure changed'):formal_objects(root,'2026-11-13')
