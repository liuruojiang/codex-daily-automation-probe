"""Failure isolation, verified recovery and scope/producer identity boundaries."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import maintain_microcap_local as local


def test_successful_issuer_survives_another_failure_and_only_failed_stage_runs(tmp_path):
    root = tmp_path / 'runtime';root.mkdir()
    journal = local.Journal(tmp_path / 'journal', root, {'target': '2026-11-06'})
    calls = []
    def stage(symbol, fail=False):
        def act():
            calls.append(symbol)
            if fail: raise TimeoutError('request failed; no placeholder publication')
            (root / (symbol + '.json')).write_text(symbol)
        def verify(): return local.hashes(root, [symbol + '.json'])
        return journal.run(symbol, act, verify)
    assert stage('a')['status'] == 'complete'
    assert stage('b', fail=True)['status'] == 'failed'
    assert stage('c')['status'] == 'complete'
    assert stage('a')['status'] == 'complete'
    assert stage('b')['status'] == 'complete'
    assert stage('c')['status'] == 'complete'
    assert calls == ['a', 'b', 'c', 'b']
    assert journal.reused == 2


def test_controller_change_revalidates_native_evidence_without_network(tmp_path):
    root = tmp_path / 'runtime';root.mkdir()
    (root / 'receipt').write_bytes(b'authentic existing source')
    def prohibited(): pytest.fail('unnecessary network refresh')
    for revision in ('old', 'fixed'):
        j = local.Journal(tmp_path / 'journal', root, {'controller': revision})
        assert j.run('price', prohibited, lambda: local.hashes(root, ['receipt']))['status'] == 'complete'


def test_completed_source_is_semantically_verified_once_and_hash_read_back(tmp_path):
    (tmp_path/'data').write_bytes(b'authenticated source')
    calls=[]
    def verify():
        calls.append(True)
        return local.hashes(tmp_path,['data'])
    j=local.Journal(tmp_path/'journal',tmp_path,{'date':'2026-11-06'})
    assert j.run('price',lambda:pytest.fail('unnecessary refresh'),verify)['status']=='complete'
    assert len(calls)==1


def test_corrupt_completed_stage_does_not_refresh_over_the_evidence(tmp_path):
    root = tmp_path / 'runtime';root.mkdir()
    (root / 'data').write_bytes(b'correct')
    expected = local.sha(b'correct')
    def verify():
        result = local.hashes(root, ['data'])
        if result['data'] != expected: raise ValueError('native data identity failed')
        return result
    j = local.Journal(tmp_path / 'journal', root, {'date': '2026-11-06'})
    assert j.run('issuer-price', lambda: None, verify)['status'] == 'complete'
    (root / 'data').write_bytes(b'corrupt')
    result = j.run('issuer-price', lambda: pytest.fail('must preserve corrupt evidence'), verify)
    assert result['status'] == 'failed' and result['error_type'] == 'ValueError'
    assert (root / 'data').read_bytes() == b'corrupt'
    # An independent issuer remains runnable.
    assert j.run('other', lambda: None, lambda: local.hashes(root, ['data']))['status'] == 'complete'


def pool(tmp_path):
    rows = {'candidates': [dict(symbol=f'{n:06d}', current_member=n <= 100) for n in range(1, 301)]}
    (tmp_path / 'candidates.json').write_bytes(local.encode(rows))
    summary = dict(schema='microcap_local_screen_preparation_v1', status='PREPARATION_VERIFIED',
                   date='2026-11-06', run_directory=str(tmp_path),
                   candidates_sha256=local.sha((tmp_path / 'candidates.json').read_bytes()))
    p = tmp_path / 'latest.json';p.write_bytes(local.encode(summary))
    return p


@pytest.mark.parametrize('target', ['2026-11-05', '2026-11-14'])
def test_pool_from_future_or_older_than_week_is_rejected(tmp_path, target):
    with pytest.raises(ValueError, match='stale or from the future'):
        local.load_pool(pool(tmp_path), target)


def test_weekly_pool_keeps_its_dated_binding_and_members(tmp_path):
    p = pool(tmp_path)
    _, symbols, _ = local.load_pool(p, '2026-11-09')
    assert len(symbols) == 300
    (tmp_path / 'candidates.json').write_bytes(b'[]')
    with pytest.raises(ValueError, match='binding'): local.load_pool(p, '2026-11-09')


@pytest.mark.parametrize('relative', ['../outside', '/absolute'])
def test_source_paths_cannot_escape_runtime(tmp_path, relative):
    with pytest.raises(ValueError): local.bound(tmp_path, relative)


def test_bound_trade_evidence_rejects_content_mismatch(tmp_path):
    (tmp_path / 'raw.json').write_bytes(b'raw')
    ref = {'path': 'raw.json', 'sha256': local.sha(b'raw')}
    assert local.read_bound_refs({'provider_queries': [ref]}, tmp_path) == {'raw.json': local.sha(b'raw')}
    (tmp_path / 'raw.json').write_bytes(b'different')
    with pytest.raises(ValueError): local.read_bound_refs(ref, tmp_path)


def test_tail_anchor_is_read_from_exact_immutable_original_price_snapshot(tmp_path):
    name = local.CACHE + 'return_basis/bundles/000001/verified/raw.csv'
    path = local.bound(tmp_path, name);path.parent.mkdir(parents=True)
    raw = b'date,close_raw\n2026-11-02,10.0\n2026-11-05,10.5\n'
    path.write_bytes(raw)
    baseline = {'files': {local.CACHE + 'prices_raw/000001.csv': local.sha(raw), name: local.sha(raw)}}
    assert local.price_seed_anchor(tmp_path, '000001', baseline) == '2026-11-05'
    path.write_bytes(raw + b'2026-11-06,10.6\n')
    with pytest.raises(ValueError, match='seed changed'):
        local.price_seed_anchor(tmp_path, '000001', baseline)
