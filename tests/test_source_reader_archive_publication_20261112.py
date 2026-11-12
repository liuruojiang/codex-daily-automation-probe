"""The new reader cannot publish an old certificate or leak its publication role."""
import importlib
import json
from pathlib import Path
import sys
import types

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
publisher = importlib.import_module('publish_microcap_source_reader_archive_20261112')
M7 = 'a7c1b9ac33454be4367880a0989dfc1466640503'
V3 = 'exact_m3_20261110_latequote_st_observation_source_only_v3'


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    review = tmp_path / 'review.json'
    review.write_text(json.dumps({'runtime_sha': M7}), encoding='utf8')
    monkeypatch.setattr(publisher.subprocess, 'check_output', lambda *a, **k: M7 + '\n')
    native = types.ModuleType('scripts.scoped_archive_source_revalidation')
    native.REVISION = V3
    monkeypatch.setitem(sys.modules, native.__name__, native)
    calls = []
    def publish(*args):
        calls.append((publisher.original.RUNTIME, publisher.original.TAG, publisher.original.NAME))
        return {'published': True}
    monkeypatch.setattr(publisher.original, 'publish', publish)
    return review, native, calls


@pytest.mark.parametrize('runtime', ['../unknown', '0' * 39, publisher.BASELINE_RUNTIME])
def test_unknown_or_old_runtime_cannot_reach_publication(tmp_path, candidate, runtime):
    review, _, calls = candidate
    review.write_text(json.dumps({'runtime_sha': runtime}), encoding='utf8')
    with pytest.raises(ValueError):
        publisher.publish(tmp_path, tmp_path / 'archive.zip', tmp_path / 'evidence', review)
    assert calls == []


def test_actual_checkout_must_match_the_review(tmp_path, candidate, monkeypatch):
    review, _, calls = candidate
    monkeypatch.setattr(publisher.subprocess, 'check_output', lambda *a, **k: '0' * 40 + '\n')
    with pytest.raises(ValueError, match='differs'):
        publisher.publish(tmp_path, tmp_path / 'archive.zip', tmp_path / 'evidence', review)
    assert calls == []


def test_original_v2_certificate_cannot_publish_as_v3(tmp_path, candidate):
    review, native, calls = candidate
    native.REVISION = 'exact_m3_20261110_boundary_coverage_source_only_v2'
    with pytest.raises(ValueError, match='v2 archive'):
        publisher.publish(tmp_path, tmp_path / 'archive.zip', tmp_path / 'evidence', review)
    assert calls == []


def test_exact_role_is_restored_after_success_and_failed_publication(tmp_path, candidate, monkeypatch):
    review, _, calls = candidate
    old = publisher.original.RUNTIME, publisher.original.TAG, publisher.original.NAME
    publisher.publish(tmp_path, tmp_path / 'archive.zip', tmp_path / 'evidence', review)
    assert calls == [(M7, 'microcap-scoped-whole-20261110-' + M7[:8],
                      'microcap-whole-delivery-state-archive-20261110-m7-observation.zip')]
    assert (publisher.original.RUNTIME, publisher.original.TAG, publisher.original.NAME) == old
    def uncertain(*args):
        raise TimeoutError('Unknown write state; reconciliation must precede retries')
    monkeypatch.setattr(publisher.original, 'publish', uncertain)
    with pytest.raises(TimeoutError):
        publisher.publish(tmp_path, tmp_path / 'archive.zip', tmp_path / 'evidence', review)
    assert (publisher.original.RUNTIME, publisher.original.TAG, publisher.original.NAME) == old
