"""Exact reader repair reuses original successful stages without re-signing them."""
import copy
import json
from pathlib import Path
import shutil
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import microcap_scoped_stage_resume as m
from prepare_microcap_local import encode, sha


def setup_case(tmp_path):
    out, consumer = tmp_path / 'delivery', tmp_path / 'consumer'
    out.mkdir(parents=True)
    consumer.mkdir()
    original = dict(schema='microcap_scoped_recovery_transaction_v1', target='2026-11-12',
                    consumer_sha=m.REVIEWED_NATIVE, source_bundle_sha256='1' * 64,
                    scope_sha256='2' * 64, parent_checkpoint_sha256='3' * 64,
                    missing_rebalances=['2026-11-12'], input_object_bindings={},
                    controller_sources=dict(m.LEGACY_CONTROLLER_SOURCES))
    current = copy.deepcopy(original)
    current['controller_sources'] = {name: sha((Path(m.__file__).parent / name).read_bytes().replace(b'\r\n', b'\n'))
                                     for name in original['controller_sources']}
    calls = []
    def run(label, argv):
        calls.append(label)
        command = out / 'commands' / label
        command.mkdir(parents=True)
        paths = {key: command / name for key, name in
                 [('stdout', 'stdout.log'), ('stderr', 'stderr.log'), ('process', 'process.json')]}
        paths['stdout'].write_bytes(b'software closed stage\n')
        paths['stderr'].write_bytes(b'')
        paths['process'].write_bytes(encode(dict(exit_code=0, command=list(map(str, argv)))))
        return paths
    return out, consumer, original, current, calls, run


def test_post_pack_failure_reuses_three_stages_then_binds_new_work_to_new_reader(tmp_path):
    out, consumer, original, current, calls, run = setup_case(tmp_path)
    data = consumer / 'formal.csv'
    data.write_bytes(b'software fixture,unchanged\n')
    stage = m.StageResume(out, consumer, original, run)
    for label in ('refresh', 'check', 'pack-formal'):
        stage.call(label, [label, consumer, out], [data])
    protected = {path: path.read_bytes() for path in (out / 'stage-receipts').glob('*.json')}
    fresh = tmp_path / 'cold'
    shutil.copytree(out, fresh / 'delivery')
    shutil.copytree(consumer, fresh / 'consumer')
    out2, consumer2 = fresh / 'delivery', fresh / 'consumer'
    def never_repeat(*args):
        raise AssertionError('A completed Native stage was repeated')
    resumed = m.StageResume(out2, consumer2, current, never_repeat)
    for label in ('refresh', 'check', 'pack-formal'):
        assert resumed.call(label, [label, consumer2, out2], [consumer2 / 'formal.csv'])['stage_reused']
    assert calls == ['refresh', 'check', 'pack-formal']
    for path, raw in protected.items():
        assert (out2 / path.relative_to(out)).read_bytes() == raw
    # Only the previously uncompleted successor executes, under current identity.
    new_work = m.StageResume(out, consumer, current, run)
    new_work.call('bootstrap-period', ['bootstrap', consumer, out], [data])
    old_receipt = json.loads((out / 'stage-receipts/refresh.json').read_bytes())
    new_receipt = json.loads((out / 'stage-receipts/bootstrap-period.json').read_bytes())
    assert old_receipt['context_sha256'] == sha(encode(original))
    assert new_receipt['context_sha256'] == sha(encode(current))
    assert (out / 'stage-receipts/context.json').read_bytes() == protected[out / 'stage-receipts/context.json']
    assert m.StageResume(out, consumer, current, never_repeat).call(
        'bootstrap-period', ['bootstrap', consumer, out], [data])['stage_reused']


@pytest.mark.parametrize('fault', ['target', 'parent', 'source', 'native', 'unknown_old_reader', 'unknown_new_reader'])
def test_reader_admission_does_not_authorize_changed_input_or_unknown_code(tmp_path, fault):
    out, consumer, original, current, calls, run = setup_case(tmp_path)
    if fault == 'unknown_old_reader':
        original['controller_sources']['microcap_recovery_handoff.py'] = 'a' * 64
    m.StageResume(out, consumer, original, run)
    if fault == 'target': current['target'] = '2026-11-13'
    if fault == 'parent': current['parent_checkpoint_sha256'] = 'b' * 64
    if fault == 'source': current['source_bundle_sha256'] = 'b' * 64
    if fault == 'native': current['consumer_sha'] = 'b' * 40
    if fault == 'unknown_new_reader': current['controller_sources']['microcap_recovery_handoff.py'] = 'b' * 64
    with pytest.raises(ValueError, match='transaction changed'):
        m.StageResume(out, consumer, current, run)
    assert not calls


@pytest.mark.parametrize('fault', ['output', 'stdout', 'admission', 'context_after_admission'])
def test_bridge_keeps_original_object_and_admission_integrity(tmp_path, fault):
    out, consumer, original, current, calls, run = setup_case(tmp_path)
    data = consumer / 'formal.csv'
    data.write_bytes(b'original fixture')
    m.StageResume(out, consumer, original, run).call('refresh', ['refresh', consumer], [data])
    resumed = m.StageResume(out, consumer, current, run)
    if fault == 'output': data.write_bytes(b'changed')
    if fault == 'stdout': (out / 'commands/refresh/stdout.log').write_bytes(b'changed')
    if fault == 'admission':
        next((out / 'stage-receipts').glob('reader-admission-*.json')).write_bytes(b'{}')
        with pytest.raises(ValueError, match='original scoped reader'):
            m.StageResume(out, consumer, current, run)
        return
    if fault == 'context_after_admission': (out / 'stage-receipts/context.json').write_bytes(b'{}')
    with pytest.raises(ValueError, match='changed'):
        resumed.call('refresh', ['refresh', consumer], [data])
    assert calls == ['refresh']


def test_rebound_legacy_handoff_is_rechecked_by_the_new_proof_reader(tmp_path):
    out, consumer, original, current, calls, run = setup_case(tmp_path)
    handoff = out / 'handoff'
    handoff.mkdir()
    cp = dict(strategy_sha=m.REVIEWED_NATIVE, handoff_mode='same_close_period_switch')
    paths = []
    for role in ('period', 'history', 'turnover', 'version_state', 'prices'):
        path = handoff / (role + '.json')
        path.write_bytes(b'{}')
        cp[role] = dict(path=path.name, sha256=sha(path.read_bytes()))
        paths.append(path)
    checkpoint = handoff / 'checkpoint.json'
    checkpoint.write_bytes(encode(cp))
    m.StageResume(out, consumer, original, run).call('handoff', ['handoff', consumer], [checkpoint, *paths])
    resumed = m.StageResume(out, consumer, current, run)
    with pytest.raises(ValueError, match='proof route'):
        resumed.call('handoff', ['handoff', consumer], [checkpoint, *paths])
    assert calls == ['handoff']


def test_first_new_refresh_keeps_its_own_closed_context_across_a_second_cold_admission(tmp_path):
    out, consumer, original, current, calls, run = setup_case(tmp_path)
    m.StageResume(out, consumer, original, run)
    data = consumer / 'formal.csv'
    data.write_bytes(b'new software refresh,original source\n')
    resumed = m.StageResume(out, consumer, current, run)
    resumed.call('refresh', ['refresh-all', consumer, out], [data])
    receipt = json.loads((out / 'stage-receipts/refresh.json').read_bytes())
    closed = {p.name: p.read_bytes() for p in (out / 'stage-receipts').glob('*.json')}
    selected = m.receipt_context(encode(original), receipt['context_sha256'], closed.__getitem__)
    assert selected == encode(current)
    assert closed['context.json'] == encode(original)
    assert receipt['context_sha256'] == sha(selected) != sha(closed['context.json'])
    assert m.receipt_context(encode(original), sha(encode(original)),
        lambda name: pytest.fail('Original receipt does not require a reader sidecar')) == encode(original)
    assert calls == ['refresh']


@pytest.mark.parametrize('fault', ['missing_context', 'missing_admission', 'context_hash', 'admission', 'changed_parent'])
def test_second_cold_admission_cannot_select_an_unproved_or_changed_reader_context(tmp_path, fault):
    out, consumer, original, current, calls, run = setup_case(tmp_path)
    m.StageResume(out, consumer, original, run)
    resumed = m.StageResume(out, consumer, current, run)
    digest = sha(encode(current))
    context_name, admission_name = 'context-reader-' + digest + '.json', 'reader-admission-' + digest + '.json'
    closed = {p.name: p.read_bytes() for p in (out / 'stage-receipts').glob('*.json')}
    if fault == 'missing_context': closed.pop(context_name)
    if fault == 'missing_admission': closed.pop(admission_name)
    if fault == 'context_hash': closed[context_name] = b'{}'
    if fault == 'admission': closed[admission_name] = b'{}'
    if fault == 'changed_parent':
        current['parent_checkpoint_sha256'] = 'f' * 64
        digest = sha(encode(current))
        closed['context-reader-' + digest + '.json'] = encode(current)
    with pytest.raises((ValueError, KeyError)):
        m.receipt_context(encode(original), digest, closed.__getitem__)
    assert not calls
