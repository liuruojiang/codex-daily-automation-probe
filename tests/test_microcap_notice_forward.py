"""Private selector fixtures; no D12 market observation or delivery claims."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import microcap_notice_prefix as m
from prepare_microcap_local import encode
from test_microcap_notice_prefix import make_notice_case,put


def make_forward_case(tmp_path,monkeypatch):
    c=make_notice_case(tmp_path,monkeypatch);m.import_prefix(**c['args'])
    def native(runtime,data,mode,profile_root=None,requested_target=None):
        if mode!='forward':return c['native'](runtime,data,mode,profile_root)
        raw=(Path(data)/m.FRESHNESS).read_bytes();active=json.loads(raw)
        result=dict(mode='forward',native_runtime=m.PRODUCER,authority=m.END,authority_day=m.TARGET,
            requested_target=requested_target,active_sha256=hashlib.sha256(raw).hexdigest(),
            old_prefix_unchanged=True,original_clocks_retained=True,state='noop',reason='no_forward_required')
        if requested_target>m.TARGET:
            if active['previous_authority']!=m.END:
                result.update(reason='already_original_advancing_checkpoint',selected_spec=m.RECEIPTS[0])
            else:
                result.update(state='select',reason='original_advancing_checkpoint_for_next_native_forward',
                    selected_spec=m.RECEIPTS[0],active_spec=m.RECEIPTS[1],selected_previous_authority=m.START,
                    source_files={spec['path']:spec['sha256'] for spec in m.RECEIPTS})
        return result
    args={k:c['args'][k] for k in ('root','protected_root','state','pool')}
    args.update(target='2026-11-12',out=tmp_path/'forward-selection',native=native)
    c.update(forward_native=native,forward_args=args)
    return c


@pytest.fixture
def forward_case(tmp_path,monkeypatch):return make_forward_case(tmp_path,monkeypatch)


def test_forward_changes_only_active_receipt_to_original_advancing_checkpoint(forward_case):
    c=forward_case;pointer=(c['root']/m.POINTER).read_bytes();self_raw=(c['root']/m.FRESHNESS).read_bytes()
    originals={n:(c['root']/n).read_bytes() for n in c['files']}
    result=m.select_forward(**c['forward_args'])
    assert result['status']=='original_notice_forward_checkpoint_selected'
    assert (c['root']/m.POINTER).read_bytes()==pointer
    assert (c['root']/m.FRESHNESS).read_bytes()==c['bodies'][m.RECEIPTS[0]['path']]
    assert (c['forward_args']['out']/'original-active-self.json').read_bytes()==self_raw
    assert all((c['root']/n).read_bytes()==raw for n,raw in originals.items())
    assert all(p.read_bytes()==raw for p,raw in c['refs'].items())
    assert result['target_freshness_granted'] is False and result['current_signal_ready'] is False


def test_same_target_keeps_the_later_self_review_untouched(forward_case):
    c=forward_case;before=m._pair(c['root'])
    result=m.select_forward(**dict(c['forward_args'],target=m.TARGET))
    assert result['status']=='notice_forward_checkpoint_not_needed' and m._pair(c['root'])==before


def test_repeated_selection_reads_back_same_checkpoint_without_second_mutation(forward_case,monkeypatch):
    c=forward_case;writes=[];write=m._write_pair_member
    monkeypatch.setattr(m,'_write_pair_member',lambda p,raw:(writes.append(str(p)),write(p,raw))[-1])
    m.select_forward(**c['forward_args']);after=m._pair(c['root'])
    second=m.select_forward(**c['forward_args'])
    assert len(writes)==1 and m._pair(c['root'])==after
    assert second['selection_reconciled'] is True and second['active_freshness_changed'] is False


def test_failed_write_with_no_effect_preserves_self_and_can_resume(forward_case,monkeypatch):
    c=forward_case;before=m._pair(c['root']);write=m._write_pair_member
    monkeypatch.setattr(m,'_write_pair_member',lambda *a:(_ for _ in ()).throw(OSError('software write denied')))
    with pytest.raises(OSError):m.select_forward(**c['forward_args'])
    assert m._pair(c['root'])==before
    monkeypatch.setattr(m,'_write_pair_member',write)
    assert m.select_forward(**c['forward_args'])['status']=='original_notice_forward_checkpoint_selected'


def test_write_response_lost_is_read_only_reconciled_not_rewritten(forward_case,monkeypatch):
    c=forward_case;writes=[];write=m._write_pair_member
    def lost(path,raw):
        writes.append(path);write(path,raw);raise OSError('software response loss after atomic replace')
    monkeypatch.setattr(m,'_write_pair_member',lost)
    result=m.select_forward(**c['forward_args'])
    assert len(writes)==1 and result['write_response_lost_reconciled'] is True
    assert json.loads((c['forward_args']['out']/'selection-intent.json').read_bytes())['status']=='complete'


@pytest.mark.parametrize('fault',['false_noop','wrong_selected_path','missing_chain','same_authority_advance','changed_self'])
def test_unknown_or_unreviewed_choice_cannot_replace_active_self(forward_case,fault):
    c=forward_case;before=m._pair(c['root']);native=c['forward_native']
    if fault=='same_authority_advance':put(c['root'],m.RECEIPTS[0]['path'],c['bodies'][m.RECEIPTS[1]['path']])
    if fault=='changed_self':put(c['root'],m.RECEIPTS[1]['path'],b'changed immutable self')
    def bad(runtime,data,mode,**kwargs):
        result=native(runtime,data,mode,**kwargs)
        if fault=='false_noop':result['state']='noop'
        if fault=='wrong_selected_path':result['selected_spec']=dict(path=m.PREFIX+'wrong/proof.json',sha256='f'*64)
        if fault=='missing_chain':result['source_files']={}
        return result
    with pytest.raises(m.NoticePrefixError):m.select_forward(**dict(c['forward_args'],native=bad))
    assert m._pair(c['root'])==before


def test_changed_owner_during_native_read_is_not_overwritten_or_rolled_back(forward_case):
    c=forward_case;native=c['forward_native'];fresh=(c['root']/m.FRESHNESS).read_bytes()
    foreign=encode(dict(manifest_path=m.PREFIX+'other/archive.json',manifest_sha256='f'*64))
    def mutate(runtime,data,mode,**kwargs):
        result=native(runtime,data,mode,**kwargs);put(c['root'],m.POINTER,foreign);return result
    with pytest.raises(m.NoticePrefixError,match='owner_or_reference'):
        m.select_forward(**dict(c['forward_args'],native=mutate))
    assert (c['root']/m.POINTER).read_bytes()==foreign and (c['root']/m.FRESHNESS).read_bytes()==fresh


@pytest.mark.parametrize('consumer_at_same_authority',[False,True])
def test_missing_receipt_is_only_noop_for_the_actual_consumer_baseline(forward_case,consumer_at_same_authority):
    c=forward_case;(c['root']/m.FRESHNESS).unlink()
    ref=m.END if consumer_at_same_authority else m.START
    put(c['protected'],m.POINTER,dict(manifest_path=ref[0],manifest_sha256=ref[1]))
    def no_active(runtime,data,mode,**kw):
        return dict(mode='forward',native_runtime=m.PRODUCER,authority=m.END,authority_day=m.TARGET,
            requested_target=kw['requested_target'],active_sha256=None,state='noop',
            reason='baseline_has_no_active_refresh_receipt',old_prefix_unchanged=True,original_clocks_retained=True)
    if consumer_at_same_authority:
        assert m.select_forward(**dict(c['forward_args'],native=no_active))['status']=='notice_forward_checkpoint_not_needed'
    else:
        with pytest.raises(m.NoticePrefixError,match='unhanded_source_authority'):
            m.select_forward(**dict(c['forward_args'],native=no_active))
    assert not (c['root']/m.FRESHNESS).exists()
