"""Synthetic packaging-protocol tests; full native data gates run in cloud acceptance."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile
import pytest
import yaml

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import microcap_whole_state_recovery as recovery

OLD='a'*40
NEW='b'*40
MANIFEST='outputs/top100_delivery_manifest.json'

def write_zip(path,manifest):
    with zipfile.ZipFile(path,'w') as archive:
        archive.writestr(MANIFEST,json.dumps(manifest))
        archive.writestr('synthetic-transport-only.txt','not a certified production state')

@pytest.fixture
def case(tmp_path,monkeypatch):
    root=tmp_path/'root';root.mkdir();(root/'outputs').mkdir()
    manifest=dict(status='complete',ok=True,scope='whole_workspace_delivery',release_sha=OLD,expected_date='2026-11-04',
                  inputs={'input.csv':'1'*64},artifacts={'v20':'2'*64,'v23':'3'*64,'v25':'4'*64},streams={'v20':{'rows':2},'v23':{'rows':2},'v25':{'rows':2}})
    bundle=tmp_path/recovery.BUNDLE_NAME;write_zip(bundle,manifest)
    (root/MANIFEST).write_text(json.dumps(manifest))
    config=tmp_path/'approved.json'
    config.write_text(json.dumps(dict(schema_version=1,strategy_sha=NEW,state_url='https://github.com/liuruojiang/microcap/releases/download/a/b.zip',state_sha256=hashlib.sha256(bundle.read_bytes()).hexdigest(),state_date='2026-11-04')))
    monkeypatch.setattr(recovery.subprocess,'check_output',lambda *a,**kw:NEW)
    calls=[]
    def pack(cmd,**kwargs):
        calls.append(cmd)
        assert cmd[cmd.index('--root')+1]==cmd[cmd.index('--v23-root')+1]==cmd[cmd.index('--v25-root')+1]==str(root)
        assert 'pack' in cmd and not any(x in cmd for x in ('refresh','sync','restore','实时信号'))
        value=copy.deepcopy(manifest);value['release_sha']=NEW
        write_zip(Path(cmd[cmd.index('--bundle')+1]),value)
    monkeypatch.setattr(recovery.subprocess,'run',pack)
    return root,bundle,config,manifest,calls,pack

def run(case):return recovery.repack_approved(*case[:2],NEW,'2026-11-04',case[2])

def test_native_pack_rebinds_only_release_and_preserves_original(case):
    original=case[1].read_bytes();value=run(case)
    assert value['repacked'] and value['economic_manifest_unchanged'] and len(case[4])==1
    assert Path(value['preserved_approved_archive']).read_bytes()==original
    assert recovery.bundle_identity(case[1],NEW,'2026-11-04')['seed_date']=='2026-11-04'
    assert json.loads((case[0]/MANIFEST).read_bytes())==case[3]

@pytest.mark.parametrize('change',['hash','config_runtime','config_day','future_ceiling','root_manifest'])
def test_unapproved_or_unrestored_input_never_runs_pack(case,change):
    root,bundle,config,manifest,calls,_=case
    conf=json.loads(config.read_bytes());ceiling='2026-11-04'
    if change=='hash':conf['state_sha256']='0'*64
    elif change=='config_runtime':conf['strategy_sha']=OLD
    elif change=='config_day':conf['state_date']='2026-11-03'
    elif change=='future_ceiling':ceiling='2026-11-03'
    else:(root/MANIFEST).write_text('{}')
    config.write_text(json.dumps(conf));original=bundle.read_bytes()
    with pytest.raises(ValueError):recovery.repack_approved(root,bundle,NEW,ceiling,config)
    assert not calls and bundle.read_bytes()==original

@pytest.mark.parametrize('key',['inputs','artifacts','streams','expected_date','release_sha'])
def test_pack_changed_economic_identity_never_replaces_original(case,monkeypatch,key):
    original=case[1].read_bytes()
    def corrupt(cmd,**kwargs):
        case[5](cmd,**kwargs);path=Path(cmd[cmd.index('--bundle')+1]);value=copy.deepcopy(case[3]);value['release_sha']=NEW
        value[key]=('2026-11-03' if key=='expected_date' else OLD if key=='release_sha' else {'changed':'x'})
        write_zip(path,value)
    monkeypatch.setattr(recovery.subprocess,'run',corrupt)
    with pytest.raises(ValueError):run(case)
    assert case[1].read_bytes()==original

def test_native_failure_keeps_original_and_partial_evidence(case,monkeypatch):
    original=case[1].read_bytes()
    def fail(cmd,**kwargs):
        Path(cmd[cmd.index('--bundle')+1]).write_bytes(b'partial diagnostic')
        raise subprocess.CalledProcessError(1,cmd)
    monkeypatch.setattr(recovery.subprocess,'run',fail)
    with pytest.raises(subprocess.CalledProcessError):run(case)
    assert case[1].read_bytes()==original
    assert list(case[1].parent.glob('approved-repack-*/native_pack.log'))

def test_exact_runtime_seed_is_reused_without_pack(case,monkeypatch):
    root,bundle,config,manifest,calls,_=case
    monkeypatch.setattr(recovery.subprocess,'check_output',lambda *a,**kw:OLD)
    conf=json.loads(config.read_bytes());conf['strategy_sha']=OLD;config.write_text(json.dumps(conf))
    before=bundle.read_bytes()
    value=recovery.repack_approved(root,bundle,OLD,'2026-11-04',config)
    assert not value['repacked'] and value['unchanged_exact_runtime'] and not calls and bundle.read_bytes()==before

@pytest.mark.parametrize('mutation',['config','root','checkout'])
def test_concurrent_identity_changes_leave_old_zip(case,monkeypatch,mutation):
    original=case[1].read_bytes()
    def change(cmd,**kwargs):
        case[5](cmd,**kwargs)
        if mutation=='config':case[2].write_text('{}')
        elif mutation=='root':(case[0]/MANIFEST).write_text('{}')
        else:monkeypatch.setattr(recovery.subprocess,'check_output',lambda *a,**kw:OLD)
    monkeypatch.setattr(recovery.subprocess,'run',change)
    with pytest.raises(ValueError):run(case)
    assert case[1].read_bytes()==original

def test_actual_yaml_only_successful_approved_bootstrap_can_repack():
    jobs=yaml.safe_load((ROOT/'.github/workflows/microcap-realtime-digest.yml').read_text())['jobs']
    steps=jobs['recovery-check']['steps'];by={s.get('id'):s for s in steps}
    step=by['approved_repack'];condition=step['if']
    required=["github.event_name == 'workflow_dispatch'","inputs.recovery_check == 'bootstrap'","inputs.validation_only != true","inputs.correction != true","inputs.external_schedule != true","inputs.delivery_test_id == ''","inputs.publication_mode == 'close_confirmed'","steps.whole_state_restore.outcome == 'success'","steps.whole_state_restore.outputs.restored == 'false'","steps.approved_bootstrap.outcome == 'success'","steps.approved_bootstrap.outputs.restored == 'true'"]
    assert condition.split(' && ')==required
    # Exercise actual YAML conjunction, including the nohit vs require_hit split.
    expr=condition
    for index,clause in enumerate(required):expr=expr.replace(clause,f'values[{index}]')
    expr=expr.replace(' && ',' and ')
    assert eval(expr,{'__builtins__':{}},{'values':[True]*len(required)})
    for index in range(len(required)):
        values=[True]*len(required);values[index]=False
        assert not eval(expr,{'__builtins__':{}},{'values':values})
    assert steps.index(by['approved_bootstrap'])<steps.index(step)
    describe=next(i for i,s in enumerate(steps) if 'recovery.py describe' in s.get('run',''))
    assert steps.index(step)<describe
    assert all('repack-approved' not in s.get('run','') for s in jobs['send']['steps'])
