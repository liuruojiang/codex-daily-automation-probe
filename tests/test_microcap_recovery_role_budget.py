"""Resource budgets only: software objects, no market data or native approval.

The small histogram fixture retains the real two central directories' counts,
path byte lengths and decimal size widths.  Generated names/digests are explicit
software fixtures; neither a verified source transport nor an admission proof.
"""
import copy
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import restore_microcap_rebalance_recovery as m
from prepare_microcap_local import encode


RUNTIME, PRODUCER, AUTO = 'a' * 40, 'b' * 40, 'c' * 40
DIGEST = '0' * 64
PREFIXES = dict(consumer='scoped-consumer/outputs/',
                producer='scoped-source-state/runtime/.microcap_index_cache/',
                other='scoped-prepare/')


def manifest(*, profile=True, phase='state'):
    plan = encode(dict(schema='microcap_scoped_recovery_plan_v1', consumer_sha=RUNTIME,
                       delivery_date='2026-11-12', required_rebalance_dates=['2026-11-12']))
    value = dict(schema=m.SCHEMA, status='resumable_partial', phase=phase,
                 runtime_sha=RUNTIME, producer_sha=PRODUCER, producer_commit=AUTO,
                 source_run_id=123, source_run_attempt=1, source_root='/private/software-fixture',
                 target='2026-11-12', required_rebalance_dates=['2026-11-12'],
                 plan_sha256=hashlib.sha256(plan).hexdigest(),
                 files={'scoped-recovery-plan.json': hashlib.sha256(plan).hexdigest()},
                 sizes={'scoped-recovery-plan.json': len(plan)}, formal_state_archive=None,
                 native_adoption_required=True, final_strategy_delivery=False,
                 email_sent=False, orders_placed=False)
    if profile:
        value.update(object_budget_profile=m.STATE_BUDGET_PROFILE,
                     object_role_counts=dict(consumer=0, producer=0, other=1))
    return value


def add_objects(value, role, count):
    for i in range(count):
        name = PREFIXES[role] + f'object-{i:06x}.raw'
        value['files'][name] = DIGEST
        value['sizes'][name] = 1
    if 'object_budget_profile' in value:
        value['object_role_counts'] = m.object_role_counts(value['files'])
    return value


def verify(value):
    return m.verify_manifest(value, RUNTIME, PRODUCER, '2026-11-12')


def test_real_two_directory_shape_exceeds_old_manifest_cap_but_fits_role_profile():
    shape = json.loads((Path(__file__).parent / 'fixtures' /
                        'microcap_recovery_role_budget_20261112.json').read_bytes())
    assert shape['sources']['producer_metadata_only'] is True
    value = manifest()
    serial = 0
    for role, bins in shape['roles'].items():
        for path_bytes, size_digits, count in bins:
            for _ in range(count):
                tail = f'{serial:06x}.r'
                name = PREFIXES[role] + 'x' * (path_bytes - len(PREFIXES[role]) - len(tail)) + tail
                assert len(name.encode()) == path_bytes
                value['files'][name] = DIGEST
                value['sizes'][name] = 10 ** (size_digits - 1)
                serial += 1
    value['object_role_counts'] = m.object_role_counts(value['files'])
    assert value['object_role_counts'] == dict(consumer=170714, producer=103543, other=3)
    assert len(value['files']) == 274260 > m.MAX_FILES
    verify(value)
    raw = encode(value)
    assert 88 * 1024**2 < len(raw) < 90 * 1024**2
    assert m.MAX_MANIFEST_BYTES < len(raw) < m.STATE_MAX_MANIFEST_BYTES
    parsed = m.parse_manifest(raw, phase='state')
    verify(parsed)
    assert parsed['files'] == value['files'] and parsed['sizes'] == value['sizes']
    # The same real-sized shape does not gain the new allowance without its
    # explicit recognized profile and accurately recomputed role counts.
    del parsed, raw
    value.pop('object_budget_profile'); value.pop('object_role_counts')
    with pytest.raises(m.RecoveryError, match='not_closed'):
        verify(value)
    with pytest.raises(m.RecoveryError, match='manifest_budget_exceeded'):
        m.parse_manifest(encode(value), phase='state')


@pytest.mark.parametrize('role,limit', [('consumer', 280000), ('producer', 150000), ('other', 30000)])
def test_each_role_limit_is_enforced_without_discarding_other_roles(role, limit):
    value = manifest()
    add_objects(value, role, limit + (0 if role == 'other' else 1))
    with pytest.raises(m.RecoveryError, match='role_object_budget_exceeded'):
        verify(value)


def test_all_role_limits_and_total_are_finite_and_allow_the_exact_boundary():
    value = manifest()
    add_objects(value, 'consumer', 280000)
    add_objects(value, 'producer', 150000)
    add_objects(value, 'other', 29999)
    assert len(value['files']) == 460000
    verify(value)
    value['files']['scoped-prepare/one-more.raw'] = DIGEST
    value['sizes']['scoped-prepare/one-more.raw'] = 1
    value['object_role_counts'] = m.object_role_counts(value['files'])
    with pytest.raises(m.RecoveryError, match='not_closed'):
        verify(value)


def test_unprofiled_legacy_state_keeps_the_old_file_limit():
    value = add_objects(manifest(profile=False), 'consumer', 250000)
    assert len(value['files']) == 250001
    with pytest.raises(m.RecoveryError, match='not_closed'):
        verify(value)


@pytest.mark.parametrize('change,error', [
    ({'object_budget_profile': 'unknown'}, 'profile_unknown'),
    ({'object_budget_profile': None}, 'profile_unknown'),
    ({'object_role_counts': {'consumer': 0, 'producer': 0, 'other': True}}, 'counts_invalid'),
    ({'object_role_counts': {'consumer': 0, 'producer': 0, 'other': -1}}, 'counts_invalid'),
    ({'object_role_counts': {'consumer': 0, 'producer': 0}}, 'counts_invalid'),
    ({'object_role_counts': {'consumer': 0, 'producer': 0, 'other': 0}}, 'counts_differ'),
])
def test_unknown_profile_and_forged_role_counts_are_rejected(change, error):
    value = manifest(); value.update(change)
    with pytest.raises(m.RecoveryError, match=error):
        verify(value)


def test_role_counts_cannot_be_carried_without_the_explicit_profile():
    value = manifest(); value.pop('object_budget_profile')
    with pytest.raises(m.RecoveryError, match='require_budget_profile'):
        verify(value)


def test_boundary_cannot_request_the_state_profile():
    with pytest.raises(m.RecoveryError, match='profile_unknown'):
        verify(manifest(phase='boundaries'))
    verify(manifest(profile=False, phase='boundaries'))


def test_profile_does_not_relax_expanded_bytes_hashes_or_paths():
    value = manifest()
    value['sizes']['scoped-recovery-plan.json'] = 16 * 1024**3 + 1
    with pytest.raises(m.RecoveryError, match='expanded_budget_exceeded'):
        verify(value)
    value = manifest(); value['files']['scoped-recovery-plan.json'] = 'not-a-hash'
    with pytest.raises(m.RecoveryError, match='object_invalid'):
        verify(value)
    value = manifest()
    value['files']['scoped-consumer/../outside.raw'] = DIGEST
    value['sizes']['scoped-consumer/../outside.raw'] = 1
    value['object_role_counts'] = m.object_role_counts(value['files'])
    with pytest.raises(m.RecoveryError, match='unsafe_object_path'):
        verify(value)
    assert m.MAX_BYTES == 2 * 1024**3 and m.MAX_EXPANDED == 16 * 1024**3


@pytest.mark.parametrize('phase,limit', [('state', 192 * 1024**2), ('boundaries', 64 * 1024**2)])
def test_raw_phase_hard_cap_rejects_before_json_decode(phase, limit):
    class TooLarge:
        def __len__(self):
            return limit + 1
    with pytest.raises(m.RecoveryError, match='manifest_budget_exceeded'):
        m.parse_manifest(TooLarge(), phase=phase)


@pytest.mark.parametrize('phase,limit', [('state', 460000), ('boundaries', 250000)])
def test_zip_phase_hard_cap_rejects_before_manifest_or_extract(tmp_path, monkeypatch, phase, limit):
    class Archive:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def infolist(self): return [None] * (limit + 257)
        def read(self, *args): raise AssertionError('budget must reject before reading JSON')
    monkeypatch.setattr(m.zipfile, 'ZipFile', lambda _: Archive())
    monkeypatch.setattr(m, 'api_request', lambda *args: object())
    monkeypatch.setattr(m, 'stream_download', lambda *args, **kwargs: {})
    item = dict(phase=phase, id=456, archive_download_url='software://fixture',
                digest='sha256:' + DIGEST, size_in_bytes=1)
    with pytest.raises(m.RecoveryError, match='archive_object_count_exceeded'):
        m._recover_candidate(item=item, runtime_sha=RUNTIME, producer_sha=PRODUCER,
                             not_after='2026-11-12', out=tmp_path/'uncreated', token='fixture', orphan_only=False)
    assert not (tmp_path/'uncreated').exists()


def test_seal_declares_recomputed_profile_but_boundary_and_old_bytes_remain_legacy(tmp_path):
    value = manifest(profile=False)
    root = tmp_path / 'origin'; root.mkdir()
    plan = encode(dict(schema='microcap_scoped_recovery_plan_v1', consumer_sha=RUNTIME,
                       delivery_date='2026-11-12', required_rebalance_dates=['2026-11-12']))
    (root/'scoped-recovery-plan.json').write_bytes(plan)
    for name, body in [('scoped-consumer/outputs/failed-refresh.stdout', b'original failure'),
                       ('scoped-source-state/runtime/.microcap_index_cache/daily_input_attempts/failure.raw', b'old failed HTTP')]:
        path = root/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(body)
    kwargs = dict(root=root, runtime_sha=RUNTIME, producer_sha=PRODUCER,
                  plan=root/'scoped-recovery-plan.json', run_id=123, run_attempt=1, producer_commit=AUTO)
    result = m.seal(**kwargs, phase='state', out=root/m.MANIFESTS['state'])
    assert result['object_budget_profile'] == m.STATE_BUDGET_PROFILE
    assert result['object_role_counts'] == dict(consumer=1, producer=1, other=1)
    assert len(result['files']) == 3
    boundary = m.seal(**kwargs, phase='boundaries', out=root/m.MANIFESTS['boundaries'])
    assert 'object_budget_profile' not in boundary and 'object_role_counts' not in boundary
    # Existing legacy seals are verified/reused as their original bytes, not
    # rewritten to make an old producer appear to have used the new profile.
    legacy = copy.deepcopy(result); legacy.pop('object_budget_profile'); legacy.pop('object_role_counts')
    original_raw = encode(legacy); (root/m.MANIFESTS['state']).write_bytes(original_raw)
    assert m.seal(**kwargs, phase='state', out=root/m.MANIFESTS['state']) == legacy
    assert (root/m.MANIFESTS['state']).read_bytes() == original_raw
