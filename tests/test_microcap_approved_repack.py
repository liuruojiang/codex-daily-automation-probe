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
        assert 'pack-archive' in cmd and not any(x in cmd for x in ('pack','refresh','sync','restore','实时信号'))
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


@pytest.mark.parametrize('mutation', ['none', 'old_hash', 'new_actual', 'other_pair', 'third_file',
                                    'missing_delta', 'data', 'during_pack', 'artifacts', 'streams'])
def test_exact_metadata_continuation_pair_keeps_all_non_source_bytes(case, monkeypatch, mutation):
    root, bundle, config, manifest, calls, _ = case
    names = ['microcap_top100_mom16_biweekly_live_v2_0.py', 'scripts/st_metadata_refresh.py']
    changes = {}
    for i, name in enumerate(names):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = ('# explicit synthetic code ' + str(i) + '\n').encode()
        path.write_bytes(raw)
        manifest['inputs'][name] = str(i + 5) * 64
        changes[name] = [manifest['inputs'][name], hashlib.sha256(raw).hexdigest()]
    write_zip(bundle, manifest)
    (root / MANIFEST).write_text(json.dumps(manifest))
    cfg = json.loads(config.read_bytes())
    cfg['state_sha256'] = hashlib.sha256(bundle.read_bytes()).hexdigest()
    cfg['source_repack_changes'] = copy.deepcopy(changes)
    if mutation == 'old_hash': cfg['source_repack_changes'][names[1]][0] = '0' * 64
    elif mutation == 'new_actual': (root / names[1]).write_bytes(b'changed')
    elif mutation == 'other_pair': cfg['source_repack_changes']['scripts/unreviewed.py'] = cfg['source_repack_changes'].pop(names[1])
    elif mutation == 'third_file': cfg['source_repack_changes']['scripts/third.py'] = ['0' * 64, '1' * 64]
    config.write_text(json.dumps(cfg))
    original = bundle.read_bytes()
    original_manifest = (root / MANIFEST).read_bytes()
    def native(cmd, **kwargs):
        calls.append(cmd)
        assert 'pack-archive' in cmd
        value = copy.deepcopy(manifest)
        value['release_sha'] = NEW
        for name, pair in changes.items(): value['inputs'][name] = pair[1]
        if mutation == 'missing_delta': value['inputs'][names[1]] = changes[names[1]][0]
        elif mutation == 'data': value['inputs']['input.csv'] = '0' * 64
        elif mutation == 'during_pack': (root / names[1]).write_bytes(b'changed during packing')
        elif mutation in ('artifacts', 'streams'): value[mutation] = {'changed': True}
        write_zip(Path(cmd[cmd.index('--bundle') + 1]), value)
    monkeypatch.setattr(recovery.subprocess, 'run', native)
    if mutation == 'none':
        result = run(case)
        assert result['verified_source_repack_changes'] == changes
        assert result['economic_manifest_unchanged'] and len(calls) == 1
        assert Path(result['preserved_approved_archive']).read_bytes() == original
    else:
        with pytest.raises(ValueError): run(case)
        assert bundle.read_bytes() == original
        if mutation in ('old_hash', 'new_actual', 'other_pair', 'third_file'): assert not calls
    assert (root / MANIFEST).read_bytes() == original_manifest

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


SOURCE='scripts/st_notice_boundary_review.py'
SOURCE_RAW=b'# synthetic approved source\nVALUE = 2\n'
SOURCE_OLD='c'*64
SOURCE_NEW=hashlib.sha256(SOURCE_RAW).hexdigest()


def source_case(case,monkeypatch):
    root,bundle,config,manifest,calls,_=case
    manifest['inputs'][SOURCE]=SOURCE_OLD
    write_zip(bundle,manifest);(root/MANIFEST).write_text(json.dumps(manifest))
    (root/'scripts').mkdir();(root/SOURCE).write_bytes(SOURCE_RAW)
    cfg=json.loads(config.read_bytes());cfg['state_sha256']=hashlib.sha256(bundle.read_bytes()).hexdigest()
    cfg['source_repack_changes']={SOURCE:[SOURCE_OLD,SOURCE_NEW]};config.write_text(json.dumps(cfg))
    def pack(cmd,**kw):
        calls.append(cmd)
        value=copy.deepcopy(manifest);value['release_sha']=NEW;value['inputs'][SOURCE]=SOURCE_NEW
        # Native implementation, not this wrapper, authenticates ancestry.
        value['previous_source_validation']={'synthetic':'native-return fixture'}
        write_zip(Path(cmd[cmd.index('--bundle')+1]),value)
    monkeypatch.setattr(recovery.subprocess,'run',pack)
    return pack


def test_exact_declared_source_native_pack_preserves_economics_and_original(case,monkeypatch):
    source_case(case,monkeypatch);before=case[1].read_bytes();manifest=case[0]/MANIFEST;manifest_before=manifest.read_bytes()
    result=run(case)
    assert result['verified_source_repack_changes']=={SOURCE:[SOURCE_OLD,SOURCE_NEW]}
    assert result['economic_manifest_unchanged'] and len(case[4])==1
    assert Path(result['preserved_approved_archive']).read_bytes()==before and manifest.read_bytes()==manifest_before
    with zipfile.ZipFile(case[1]) as z:value=json.loads(z.read(MANIFEST))
    assert value['previous_source_validation']=={'synthetic':'native-return fixture'}
    for key in ('artifacts','streams','expected_date'):assert value[key]==case[3][key]


@pytest.mark.parametrize('declaration',[
    None,[], {SOURCE:[SOURCE_OLD,SOURCE_OLD]}, {SOURCE:['bad',SOURCE_NEW]},
    {SOURCE:[SOURCE_OLD,SOURCE_NEW,'extra']}, {SOURCE:{'old':SOURCE_OLD,'new':SOURCE_NEW}},
    {'data/price.py':[SOURCE_OLD,SOURCE_NEW]}, {'outputs/manifest.py':[SOURCE_OLD,SOURCE_NEW]},
    {'.microcap_index_cache/cache.py':[SOURCE_OLD,SOURCE_NEW]}, {'scripts/../data.py':[SOURCE_OLD,SOURCE_NEW]},
    {'scripts/nested/test.py':[SOURCE_OLD,SOURCE_NEW]}, {'scripts/a.py':[SOURCE_OLD,SOURCE_NEW],SOURCE:[SOURCE_OLD,SOURCE_NEW]},
])
def test_invalid_source_declaration_fails_before_native_pack(case,monkeypatch,declaration):
    source_case(case,monkeypatch);config=case[2];cfg=json.loads(config.read_bytes());cfg['source_repack_changes']=declaration;config.write_text(json.dumps(cfg))
    before=case[1].read_bytes()
    with pytest.raises(ValueError):run(case)
    assert not case[4] and case[1].read_bytes()==before


@pytest.mark.parametrize('mutation',['undeclared','extra_key','missing_key','data_change','wrong_new','missing_declared_change','artifacts','streams','expected_date'])
def test_native_result_must_match_exact_declared_delta(case,monkeypatch,mutation):
    pack=source_case(case,monkeypatch);original=case[1].read_bytes()
    if mutation=='undeclared':
        cfg=json.loads(case[2].read_bytes());cfg.pop('source_repack_changes');case[2].write_text(json.dumps(cfg))
    def bad(cmd,**kw):
        pack(cmd,**kw);path=Path(cmd[cmd.index('--bundle')+1])
        with zipfile.ZipFile(path) as z:value=json.loads(z.read(MANIFEST))
        if mutation=='extra_key':value['inputs']['scripts/extra.py']='d'*64
        if mutation=='missing_key':value['inputs'].pop('input.csv')
        if mutation=='data_change':value['inputs']['input.csv']='d'*64
        if mutation=='wrong_new':value['inputs'][SOURCE]='d'*64
        if mutation=='missing_declared_change':value['inputs'][SOURCE]=SOURCE_OLD
        if mutation in ('artifacts','streams'):value[mutation]={'corrupt':True}
        if mutation=='expected_date':value['expected_date']='2026-11-03'
        write_zip(path,value)
    monkeypatch.setattr(recovery.subprocess,'run',bad)
    with pytest.raises(ValueError):run(case)
    assert case[1].read_bytes()==original


@pytest.mark.parametrize('mutation',['old_hash','new_actual_bytes','source_changes_during_pack','same_pin'])
def test_declared_source_binds_real_bytes_old_input_and_new_runtime(case,monkeypatch,mutation):
    pack=source_case(case,monkeypatch);before=case[1].read_bytes()
    if mutation=='old_hash':
        cfg=json.loads(case[2].read_bytes());cfg['source_repack_changes'][SOURCE][0]='d'*64;case[2].write_text(json.dumps(cfg))
    elif mutation=='new_actual_bytes':(case[0]/SOURCE).write_bytes(b'tampered')
    elif mutation=='source_changes_during_pack':
        def changed(cmd,**kw):pack(cmd,**kw);(case[0]/SOURCE).write_bytes(b'tampered')
        monkeypatch.setattr(recovery.subprocess,'run',changed)
    else:
        cfg=json.loads(case[2].read_bytes());cfg['strategy_sha']=OLD;case[2].write_text(json.dumps(cfg))
        monkeypatch.setattr(recovery.subprocess,'check_output',lambda *a,**k:OLD)
    with pytest.raises(ValueError):
        recovery.repack_approved(case[0],case[1],OLD if mutation=='same_pin' else NEW,'2026-11-04',case[2])
    assert case[1].read_bytes()==before


def test_declared_source_uses_native_lf_hash_contract(case,monkeypatch):
    source_case(case,monkeypatch);(case[0]/SOURCE).write_bytes(SOURCE_RAW.replace(b'\n',b'\r\n'))
    assert run(case)['verified_source_repack_changes'][SOURCE][1]==SOURCE_NEW


@pytest.mark.parametrize("fault", [None, "old_hash", "new_bytes", "during_pack", "economic_change"])
def test_exact_v20_execution_source_transition_keeps_all_bindings(case, monkeypatch, fault):
    import sys
    monkeypatch.setattr(sys.modules[__name__], "SOURCE", "microcap_top100_mom16_biweekly_live_v2_0.py")
    pack = source_case(case, monkeypatch)
    before = case[1].read_bytes()
    if fault == "old_hash":
        cfg=json.loads(case[2].read_bytes());cfg['source_repack_changes'][SOURCE][0]='d'*64;case[2].write_text(json.dumps(cfg))
    elif fault == "new_bytes": (case[0]/SOURCE).write_bytes(b'changed')
    elif fault in ("during_pack", "economic_change"):
        def changed(cmd, **kw):
            pack(cmd, **kw)
            if fault == "during_pack": (case[0]/SOURCE).write_bytes(b'changed')
            else:
                p=Path(cmd[cmd.index('--bundle')+1])
                with zipfile.ZipFile(p) as z: value=json.loads(z.read(MANIFEST))
                value['inputs']['input.csv']='d'*64;write_zip(p,value)
        monkeypatch.setattr(recovery.subprocess, 'run', changed)
    if fault:
        with pytest.raises(ValueError): run(case)
        assert case[1].read_bytes()==before
    else:
        result=run(case)
        assert result['verified_source_repack_changes']=={SOURCE:[SOURCE_OLD,SOURCE_NEW]}
        assert result['economic_manifest_unchanged']
        assert Path(result['preserved_approved_archive']).read_bytes()==before


@pytest.mark.parametrize("path", ["analyze_top100_rebalance_frequency.py", "microcap_top100_mom16_biweekly_live_v2_3.py", "../microcap_top100_mom16_biweekly_live_v2_0.py", "outputs/microcap_top100_mom16_biweekly_live_v2_0.py"])
def test_v20_exception_does_not_open_other_root_or_data_paths(path):
    with pytest.raises(ValueError):
        recovery._source_repack_changes(json.dumps({'source_repack_changes':{path:[SOURCE_OLD,SOURCE_NEW]}}).encode())
