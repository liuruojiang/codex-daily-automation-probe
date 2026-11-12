"""Real immutable M7 walker reproduction and explicit Auto-forward fixtures.

The two D11 proof files are original public raw evidence. The D12 leaf below
is deliberately software-only. Reaching the Native verification gate does not
claim that its missing D12 source closure or a formal market gate passed.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import microcap_notice_prefix as m
from prepare_microcap_local import encode
from test_microcap_notice_prefix import put,notice_case
from test_microcap_notice_forward import forward_case
from test_microcap_notice_prefix_adversarial import prepare_pipeline

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'tests/fixtures/microcap_notice_prefix_20261111'
ADVANCE='1afcedebbb8e0e2adbe1a529ffbb7b1d3728ec15fe91c0f8550c3daa1bfe672c'
SELF='651db4226dbdb200222be74017b7ca332adb838bd57bec213f18670a809a256b'
M7='a7c1b9ac33454be4367880a0989dfc1466640503'

CHILD=r'''
import asyncio,hashlib,json,socket,subprocess,sys,urllib.request
from pathlib import Path
native,source,out=map(Path,sys.argv[1:])
expected='a7c1b9ac33454be4367880a0989dfc1466640503'
assert subprocess.check_output(['git','-C',str(native),'rev-parse','HEAD'],text=True).strip()==expected
name='scripts/scoped_native_consumer.py'
original=subprocess.check_output(['git','-C',str(native),'show',expected+':'+name])
assert original.replace(b'\r\n',b'\n')==(native/name).read_bytes().replace(b'\r\n',b'\n')
def forbidden(*a,**k):raise AssertionError('No network or nested process belongs in this walker test')
socket.create_connection=socket.getaddrinfo=socket.socket.connect=socket.socket.connect_ex=urllib.request.urlopen=forbidden
subprocess.Popen=forbidden
sys.path.insert(0,str(native))
from scripts import scoped_native_consumer as module
assert Path(module.__file__).resolve()==(native/name).resolve()
advance=source/'advance-proof.json';self_proof=source/'self-proof.json'
advance_raw=advance.read_bytes();self_raw=self_proof.read_bytes()
first=json.loads(advance_raw);same=json.loads(self_raw)
previous=first['previous_authority'];endpoint=first['authority']
assert same['previous_authority']==same['authority']==endpoint
prefix='.microcap_index_cache/st_notice_sources/'
advance_path=source/endpoint[0];advance_path=advance_path.with_name('proof.json')
self_path=source/(prefix+'refresh_20261112T010443196822Z_1489267b2428/proof.json')
leaf=source/(prefix+'refresh_explicit_software_d12/proof.json')
for path,raw in ((advance_path,advance_raw),(self_path,self_raw)):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
leaf.parent.mkdir(parents=True,exist_ok=True)
software=dict(schema='explicit_software_D12_walker_leaf_not_a_native_market_proof',target='2026-11-12',
    previous_authority=endpoint,authority=[prefix+'refresh_explicit_software_d12/archive.json','f'*64],
    required_asof='2026-11-12T07:26:00+00:00',completed_at='2026-11-12T07:27:00+00:00')
leaf_raw=(json.dumps(software)+'\n').encode();leaf.write_bytes(leaf_raw)
active=source/module.FRESHNESS;active.write_bytes(leaf_raw)
backup=leaf.with_name('previous_freshness.json');backup.write_bytes(self_raw)
class ReachedVerificationGate(Exception):pass
calls=[]
def native_gate_not_executed(root,prior,receipts,target):
    calls.append(dict(previous=prior,receipts=receipts,target=target))
    raise ReachedVerificationGate('D12 verification deliberately not executed without authentic source closure')
module.sources.verify_notice_chain=native_gate_not_executed
try:module.original_notice_chain(source,previous,'2026-11-12')
except ValueError as exc:
    assert str(exc)=='Original older notice proof is unavailable',str(exc)
else:raise AssertionError('Original M7 walker must reproduce the self-review predecessor failure')
assert calls==[]
# This is the exact backup that unchanged Native refresh creates after the Auto
# selector chooses the original advancing proof as the active D11 predecessor.
backup.write_bytes(advance_raw)
try:module.original_notice_chain(source,previous,'2026-11-12')
except ReachedVerificationGate:pass
else:raise AssertionError('Advancing proof predecessor must reach the original Native gate')
assert len(calls)==1 and calls[0]['target']=='2026-11-12'
assert len(calls[0]['receipts'])==2
assert calls[0]['receipts'][0]['sha256']==hashlib.sha256(advance_raw).hexdigest()
assert calls[0]['receipts'][1]['sha256']==hashlib.sha256(leaf_raw).hexdigest()
assert advance_path.read_bytes()==advance_raw and self_path.read_bytes()==self_raw
result=dict(schema='real_m7_notice_walker_counterexample_v1',native_sha=expected,
    original_native_module=str(module.__file__),native_source_sha256=hashlib.sha256(original).hexdigest(),
    original_self_review_predecessor_failed_before_gate=True,
    original_error='Original older notice proof is unavailable',
    advancing_original_predecessor_reached_gate=True,verification_gate_called=True,
    verification_gate_executed=False,D12_formal_gate_passed=False,software_D12_leaf=True,
    original_D11_advance_sha256=hashlib.sha256(advance_raw).hexdigest(),
    original_D11_self_sha256=hashlib.sha256(self_raw).hexdigest(),
    two_original_receipts_unchanged=True,source_market_requests=0,SMTP_calls=0,production_changed=False)
out.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
print(json.dumps(result))
'''


def test_real_M7_original_notice_chain_reproduces_self_review_break_and_reaches_gate_with_original_advance(
        tmp_path,record_property):
    native=Path(os.environ.get('MICROCAP_NATIVE_ROOT') or os.environ.get('MICROCAP_RECOVERY_NATIVE_ROOT')
        or r'D:\Codex\workspaces\microcap-source-reader-m7-20261112').resolve()
    assert native.is_dir(),'Exact M7 source checkout is required'
    source=tmp_path/'walker';source.mkdir()
    for name,digest in (('advance-proof.json',ADVANCE),('self-proof.json',SELF)):
        raw=(FIXTURE/name).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==digest
        (source/name).write_bytes(raw)
    process=subprocess.run([sys.executable,'-B','-X','utf8','-c',CHILD,str(native),str(source),str(tmp_path/'result.json')],
        capture_output=True,text=True,encoding='utf-8')
    assert process.returncode==0,process.stdout+'\n'+process.stderr
    result=json.loads(process.stdout.strip().splitlines()[-1])
    assert result['original_self_review_predecessor_failed_before_gate'] is True
    assert result['advancing_original_predecessor_reached_gate'] is True
    assert result['verification_gate_executed'] is False and result['D12_formal_gate_passed'] is False
    record_property('real_native_walker_boundary',json.dumps(result))


def protected_bytes(c):
    return {path:path.read_bytes() for path in c['refs']}


def crash_after_selection(c,monkeypatch):
    atomic=m.atomic;intent=c['forward_args']['out']/'selection-intent.json'
    def interrupted(path,raw):
        if Path(path)==intent and json.loads(raw).get('status')=='complete':
            raise RuntimeError('explicit software process interruption before complete journal')
        return atomic(path,raw)
    monkeypatch.setattr(m,'atomic',interrupted)
    with pytest.raises(RuntimeError,match='before complete journal'):
        m.select_forward(**c['forward_args'])
    monkeypatch.setattr(m,'atomic',atomic)
    assert json.loads(intent.read_bytes())['status']=='intent'
    return intent


def test_forward_selection_preserves_both_original_records_and_never_advances_authority(forward_case):
    c=forward_case;pointer=(c['root']/m.POINTER).read_bytes();self_raw=(c['root']/m.FRESHNESS).read_bytes()
    immutable={name:(c['root']/name).read_bytes() for name in c['files']};anchors=protected_bytes(c)
    result=m.select_forward(**c['forward_args'])
    assert result['status']=='original_notice_forward_checkpoint_selected'
    assert (c['root']/m.POINTER).read_bytes()==pointer
    assert (c['root']/m.FRESHNESS).read_bytes()==immutable[m.RECEIPTS[0]['path']]
    assert (c['forward_args']['out']/'original-active-self.json').read_bytes()==self_raw
    assert all((c['root']/name).read_bytes()==raw for name,raw in immutable.items())
    assert all(path.read_bytes()==raw for path,raw in anchors.items())
    assert result['target_freshness_granted'] is result['current_signal_ready'] is result['final_strategy_delivery'] is False
    assert result['original_active_sha256']==hashlib.sha256(self_raw).hexdigest()


def test_same_day_more_recent_self_review_remains_exactly_active(forward_case):
    c=forward_case;later=json.loads((c['root']/m.FRESHNESS).read_bytes())
    later.update(required_asof='2026-11-12T03:00:00+00:00',completed_at='2026-11-12T03:01:00+00:00')
    later_raw=encode(later)
    put(c['root'],m.PREFIX+'later/proof.json',later_raw);put(c['root'],m.FRESHNESS,later_raw)
    before=m._pair(c['root'])
    result=m.select_forward(**dict(c['forward_args'],target='2026-11-11'))
    assert result['status']=='notice_forward_checkpoint_not_needed' and m._pair(c['root'])==before
    assert not (c['forward_args']['out']/'original-active-self.json').exists()


def test_absent_original_advancing_proof_never_rewrites_the_active_self(forward_case):
    c=forward_case;before=m._pair(c['root']);anchors=protected_bytes(c)
    (c['root']/m.RECEIPTS[0]['path']).unlink()
    with pytest.raises((m.NoticePrefixError,FileNotFoundError)):
        m.select_forward(**c['forward_args'])
    assert m._pair(c['root'])==before and not (c['forward_args']['out']/'receipt.json').exists()
    assert all(path.read_bytes()==raw for path,raw in anchors.items())


@pytest.mark.parametrize('fault',['advance_conflict','self_conflict'])
def test_conflicting_immutable_original_is_not_repaired_by_overwriting_it(forward_case,fault):
    c=forward_case;before=m._pair(c['root'])
    path=c['root']/m.RECEIPTS[0 if fault=='advance_conflict' else 1]['path']
    value=json.loads(path.read_bytes());value['completed_at']='2026-11-12T04:00:00+00:00'
    foreign=encode(value);path.write_bytes(foreign)
    with pytest.raises(m.NoticePrefixError):m.select_forward(**c['forward_args'])
    assert path.read_bytes()==foreign and m._pair(c['root'])==before
    assert not (c['forward_args']['out']/'receipt.json').exists()


def test_interrupted_after_real_replace_reconciles_pending_intent_without_second_write(forward_case,monkeypatch):
    c=forward_case;self_raw=(c['root']/m.FRESHNESS).read_bytes();pointer=(c['root']/m.POINTER).read_bytes()
    intent=crash_after_selection(c,monkeypatch)
    assert (c['root']/m.FRESHNESS).read_bytes()==c['bodies'][m.RECEIPTS[0]['path']]
    monkeypatch.setattr(m,'_write_pair_member',lambda *a:pytest.fail('Committed selection must only be read back'))
    result=m.select_forward(**c['forward_args'])
    assert result['selection_reconciled'] is True and result['active_freshness_changed'] is False
    assert json.loads(intent.read_bytes())['status']=='reconciled'
    assert (c['root']/m.POINTER).read_bytes()==pointer
    assert (c['forward_args']['out']/'original-active-self.json').read_bytes()==self_raw


@pytest.mark.parametrize('fault',['missing','changed'])
def test_pending_intent_cannot_claim_success_after_losing_original_self_trace(forward_case,monkeypatch,fault):
    c=forward_case;intent=crash_after_selection(c,monkeypatch);after=m._pair(c['root'])
    backup=c['forward_args']['out']/'original-active-self.json'
    if fault=='missing':backup.unlink()
    else:backup.write_bytes(b'unknown original self bytes\n')
    with pytest.raises(m.NoticePrefixError,match='original_self_evidence_missing'):
        m.select_forward(**c['forward_args'])
    assert m._pair(c['root'])==after and json.loads(intent.read_bytes())['status']=='intent'
    assert not (c['forward_args']['out']/'receipt.json').exists()


@pytest.mark.parametrize('same_protected_authority',[False,True])
def test_missing_active_proof_is_allowed_only_at_the_actual_formal_consumer_baseline(forward_case,same_protected_authority):
    c=forward_case;(c['root']/m.FRESHNESS).unlink()
    authority=m.END if same_protected_authority else m.START
    put(c['protected'],m.POINTER,dict(manifest_path=authority[0],manifest_sha256=authority[1]))
    before=m._pair(c['root'])
    def original_baseline(runtime,data,mode,profile_root=None,requested_target=None):
        assert mode=='forward'
        return dict(mode='forward',native_runtime=m.PRODUCER,authority=m.END,authority_day=m.TARGET,
            requested_target=requested_target,active_sha256=None,state='noop',reason='baseline_has_no_active_refresh_receipt',
            old_prefix_unchanged=True,original_clocks_retained=True)
    if same_protected_authority:
        result=m.select_forward(**dict(c['forward_args'],native=original_baseline))
        assert result['status']=='notice_forward_checkpoint_not_needed'
    else:
        with pytest.raises(m.NoticePrefixError,match='missing_active_receipt_for_unhanded_source_authority'):
            m.select_forward(**dict(c['forward_args'],native=original_baseline))
    assert m._pair(c['root'])==before


def test_formal_consumer_role_is_rejected_before_any_forward_source_selection(forward_case):
    c=forward_case;anchors=protected_bytes(c);pair=m._pair(c['root'])
    with pytest.raises(m.NoticePrefixError,match='private_separate_roles'):
        m.select_forward(**dict(c['forward_args'],root=c['protected']))
    assert m._pair(c['root'])==pair and all(path.read_bytes()==raw for path,raw in anchors.items())


def test_actual_prepare_selects_original_checkpoint_between_sidecar_warm_and_D12_maintenance(prepare_pipeline):
    c,runner,args,calls,_=prepare_pipeline
    result=runner.prepare(**args)
    labels=[label for label,_ in calls]
    assert labels.index('warm-approved-notice-prefix')<labels.index('select-original-notice-forward-checkpoint')<labels.index('maintain')
    active=json.loads((c['root']/m.FRESHNESS).read_bytes())
    assert active['previous_authority']==m.START and active['authority']==m.END and active['target']=='2026-11-11'
    assert (c['root']/m.RECEIPTS[1]['path']).read_bytes()==c['bodies'][m.RECEIPTS[1]['path']]
    names=[name for name in result['files'] if name.startswith('receipts/notice-forward-')]
    assert len(names)==1
    proof=json.loads((args['out']/names[0]).read_bytes())
    assert proof['authority_pointer_unchanged'] is True and proof['target_freshness_granted'] is False
    assert result['target']=='2026-11-12' and result['formal_release_verified'] is False
