"""Launcher control tests; no strategy suite, market data or production writes."""
import copy,importlib.util,json,os,sys,time,subprocess
from pathlib import Path
from datetime import datetime,timedelta
import pytest
HERE=Path(__file__).parent
spec=importlib.util.spec_from_file_location('core_resume_candidate',HERE/'realtime_daily_gate.py')
g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
SOURCE=Path(g.CORE_RESUME_REVIEW['gate']);PREPARED=Path('D:/CodexDaily/flow9')
HOME=Path('D:/Codex/home/automations/ic-im')
KEYS=('worker_sha256','group_worker_sha256','pytest_config_sha256','microcap_readiness_sha256','resident_worker_sha256','guard_source_sha256','config_sha256','runtime')
def actual_args():
 old=g._read_json(SOURCE);receipt=g._read_json(PREPARED/'prepared.json')
 return old,receipt,old['observed_automation_main'],{k:(g.CORE_RESUME_REVIEW['scheduling_only_worker_change']['after'] if k=='group_worker_sha256' else old[k]) for k in KEYS}
def test_actual_archive_and_all_four_successful_groups_reverified_readonly():
 old,receipt,remote,bindings=actual_args()
 result,evidence=g.validate_core_resume(SOURCE,PREPARED,SOURCE.parent.with_name('never_started_control'),receipt,remote,bindings)
 assert result==old and evidence['evidence_files']==144
 assert next(x for x in old['microcap'] if x['label']=='core30')['pass'] is False
 assert len(g._read_json(SOURCE.parent/'core30/expected_collection.json')['selected'])==1081
@pytest.mark.parametrize('artifact',['gate.json','run.json','evidence-manifest.json','acceptance.zip','release6/regression.xml','share_source2/collection.json','icim_results/receipt.json'])
def test_mutated_original_evidence_rejected(monkeypatch,artifact):
 original=g.sha
 monkeypatch.setattr(g,'sha',lambda p:'0'*64 if Path(p).as_posix().endswith('/'+artifact) else original(p))
 with pytest.raises(ValueError):g._resume_archive(SOURCE,g.CORE_RESUME_REVIEW)
@pytest.mark.parametrize('change',['launcher','remote','prepared','consumer','runtime','worker','group_worker','config','passed','calendar','already_resumed'])
def test_identity_changes_reject_before_any_group_reuse(monkeypatch,change):
 old,receipt,remote,bindings=actual_args()
 if change=='launcher':old['launcher_sha256']='unknown'
 if change=='remote':remote='unknown'
 if change=='prepared':old['prepared_receipt_sha256']='unknown'
 if change=='consumer':receipt['automation_pin']='unknown'
 if change=='runtime':bindings['runtime']={}
 if change=='worker':bindings['worker_sha256']='unknown'
 if change=='group_worker':bindings['group_worker_sha256']='unknown'
 if change=='config':bindings['config_sha256']='unknown'
 if change=='passed':old['pass']=True
 if change=='calendar':old['calendar']={}
 if change=='already_resumed':old['resume']={"old":True}
 reader=g._read_json
 monkeypatch.setattr(g,'_resume_archive',lambda *a:{})
 monkeypatch.setattr(g,'_resume_processes_finished',lambda *a:None)
 monkeypatch.setattr(g,'_read_json',lambda p:old if Path(p)==SOURCE else reader(p))
 monkeypatch.setattr(g,'_resume_group',lambda *a,**kw:pytest.fail('bad identity must reject first'))
 with pytest.raises(ValueError):g.validate_core_resume(SOURCE,PREPARED,SOURCE.parent.with_name('never_started_control'),receipt,remote,bindings)
@pytest.mark.parametrize('field,value',[('exit_code',1),('timed_out',False),('termination_confirmed',False),('hard_timeout_seconds',301)])
def test_original_timeout_must_be_exact_and_terminated(monkeypatch,field,value):
 old,receipt,remote,bindings=actual_args();core=next(x for x in old['microcap'] if x['label']=='core30');core[field]=value
 reader=g._read_json
 def read(p):
  p=Path(p)
  if p==SOURCE:return old
  if p==SOURCE.parent/'core30/gate.json':return core
  result=reader(p)
  if p==SOURCE.parent/'core30/process.json':result[field]=value
  return result
 monkeypatch.setattr(g,'_resume_archive',lambda *a:{})
 monkeypatch.setattr(g,'_resume_processes_finished',lambda *a:None)
 monkeypatch.setattr(g,'_read_json',read)
 with pytest.raises(ValueError,match='timeout'):g.validate_core_resume(SOURCE,PREPARED,SOURCE.parent.with_name('never_started_control'),receipt,remote,bindings)
@pytest.mark.parametrize('fault',['exit','counts','executed','deselected','guard'])
def test_reused_share_group_is_reverified_not_merely_pass_true(monkeypatch,fault):
 old,_,_,_=actual_args();expected=next(x for x in old['microcap'] if x['label']=='share_source2');reader=g._read_json
 def read(p):
  x=reader(p)
  if fault=='exit' and Path(p).name=='process.json':x['exit_code']=1
  if fault=='executed' and Path(p).name=='collection.json':x['executed']=x['executed'][:-1]
  if fault=='deselected' and Path(p).name=='collection.json':x['deselected']=['unapproved']
  return x
 monkeypatch.setattr(g,'_read_json',read)
 if fault=='counts':
  counts=g.junit_counts
  def changed(*a):
   x=counts(*a);x['errors']=1;return x
  monkeypatch.setattr(g,'junit_counts',changed)
 if fault=='guard':monkeypatch.setattr(g,'guard_evidence',lambda *a:(True,['NETWORK_BLOCKED']))
 with pytest.raises(ValueError):g._resume_group(SOURCE.parent,'share_source2',expected,['tests/'+x for x in g.SHARE_SOURCE])
def test_running_original_process_rejected_without_killing():
 import psutil
 current={'pid':os.getpid(),'created':psutil.Process().create_time()}
 with pytest.raises(ValueError,match='still running'):g._resume_processes_finished({'owner':current,'children':[]})
def test_unknown_process_status_rejected(monkeypatch):
 import ctypes,types
 class F:
  def __init__(self,value):self.value=value
  def __call__(self,*a):return self.value
 api=types.SimpleNamespace(OpenProcess=F(0),WaitForSingleObject=F(0),GetProcessTimes=F(0),CloseHandle=F(0))
 monkeypatch.setattr(ctypes,'WinDLL',lambda *a,**kw:api)
 monkeypatch.setattr(ctypes,'get_last_error',lambda:5)
 with pytest.raises(ValueError,match='unknown'):g._resume_processes_finished({'owner':{'pid':123,'created':1.0},'children':[]})
@pytest.mark.parametrize('mode',['daily','activate','prepare'])
def test_daily_and_other_modes_cannot_resume(monkeypatch,mode):
 monkeypatch.setattr(sys,'argv',['gate.py',mode,'--resume-from',str(SOURCE)])
 with pytest.raises(SystemExit) as exc:g.main()
 assert exc.value.code==2
@pytest.mark.parametrize('out',[SOURCE.parent,SOURCE.parent/'child',SOURCE.parent.parent])
def test_output_must_not_overlap_original_evidence(out):
 with pytest.raises(ValueError,match='overlap'):g.validate_core_resume(SOURCE,PREPARED,out,{},None,{})
@pytest.mark.parametrize('fault',[None,'failed','missing_node','changed_node','timeout_missing_collection'])
def test_only_entire_core_runs_and_four_successes_are_reused(tmp_path,monkeypatch,fault):
 old,receipt,remote,_=actual_args();collection=g._read_json(SOURCE.parent/'core30/expected_collection.json');nodes=collection['selected']
 monkeypatch.setattr(g,'HOME',HOME);monkeypatch.setattr(g,'CONFIG',HOME/'scripts/realtime_daily_gate.json')
 monkeypatch.setattr(g,'validate_prepared',lambda target:receipt)
 monkeypatch.setattr(g,'verify_production_sources',lambda x:None);monkeypatch.setattr(g,'verify_remote',lambda x:remote)
 monkeypatch.setattr(g,'validate_core_resume',lambda *a:(old,{'gate_path':str(SOURCE),'gate_sha256':g.CORE_RESUME_REVIEW['gate_sha256']}))
 monkeypatch.setattr(g,'_resume_archive',lambda *a:{})
 monkeypatch.setattr(g,'_resume_processes_finished',lambda *a:None)
 monkeypatch.setattr(g,'icim_supplemental_group',lambda *a:pytest.fail('successful5 must not rerun'))
 calls=[]
 def launch(cmd,cwd,env,out,label,timeout):
  calls.append(label);assert label=='core30' and timeout==300
  assert cmd[6:]==['tests/'+name for name in g.CORE]
  assert Path(cmd[4]).name=='daily_microcap_group_worker.py'
  assert env['TOP100_REALTIME_REQUIRE_STATE']=='1' and env['PYTEST_DISABLE_PLUGIN_AUTOLOAD']=='1'
  assert env['TEMP']==env['TMP']==str(out.parent/'core30_temp')
  out.mkdir()
  if fault=='timeout_missing_collection':
   # Actual failed process shape, not an executed strategy child in this test.
   process=g._read_json(Path('D:/ct/icim_gate_20261105/flow8_core_resume/core30/process.json'))
   assert process['exit_code']==124 and process['timed_out'] is True and process['termination_confirmed'] is True
   assert process['hard_timeout_seconds']==300 and process['elapsed_seconds']>=300
   process.update(command=cmd,cwd=str(cwd))
   g.write(out/'process.json',process)
   return process
  (out/'regression.xml').write_text('<testsuites/>')
  selected=list(nodes)
  if fault=='missing_node':selected.pop()
  if fault=='changed_node':selected[-1]+='unapproved'
  g.write(out/'collection.json',dict(collected=selected,selected=selected,executed=selected,deselected=[]))
  return dict(label=label,exit_code=1 if fault=='failed' else 0)
 monkeypatch.setattr(g,'subprocess_gate',launch)
 monkeypatch.setattr(g,'junit_counts',lambda *a:dict(tests=len(nodes),failures=0,errors=0,skipped=0))
 monkeypatch.setattr(g,'validate_collection',lambda *a:None)
 monkeypatch.setattr(g,'guard_evidence',lambda *a:(True,[]));monkeypatch.setattr(g,'UNCONFIRMED_CHILDREN',[])
 if fault in ('missing_node','changed_node'):
  with pytest.raises(ValueError,match='entire core30'):g.gate(PREPARED,tmp_path/'new',resume_from=SOURCE)
  assert not (tmp_path/'new/gate.json').exists()
 else:
  result=g.gate(PREPARED,tmp_path/'new',resume_from=SOURCE)
  assert calls==['core30'] and result['pass'] is (fault is None)
  assert result['started_at']==old['started_at'] and result['calendar'] is None and result['release_for_intraday'] is False
  assert result['icim']==old['icim'] and result['icim_supplemental5']==old['icim_supplemental5']
  assert result['microcap'][1:]==old['microcap'][1:]
  if fault=='timeout_missing_collection':
   stored=g._read_json(tmp_path/'new/gate.json')
   assert stored==result and stored['pass'] is False
   core=stored['microcap'][0]
   assert core['exit_code']==124 and core['timed_out'] is True and core['termination_confirmed'] is True
   assert core['resume_collection_status']=='not_generated_after_failed_core'
   assert 'cannot authorize success' in core['resume_failure_reason']
   assert not (tmp_path/'new/core30/collection.json').exists()
  assert result['resume']['rerun_groups']==['core30']
  assert set(result['resume']['reused_groups'])=={'release6','share_source2','icim47','icim_supplemental5'}
def test_unknown_source_has_no_new_resume_authorization(tmp_path):
 with pytest.raises(ValueError):g._resume_archive(tmp_path/'gate.json',g.CORE_RESUME_REVIEW)
