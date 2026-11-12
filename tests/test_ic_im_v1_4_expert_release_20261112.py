"""Reject incomplete release support and keep the approved calendar's boundaries."""
from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build_ic_im_v1_4_digest as digest
import ic_im_v1_4_delivery_contract as contract
import ic_im_state_council_calendar_v1 as calendar
import verify_icim_source_release as verifier

MANIFEST = 'docs/ic_im_expert_release_20261112_manifest.json'
COMMIT = 'a' * 40


def source_proof():
    raw = (ROOT / MANIFEST).read_bytes()
    value = json.loads(raw)
    return {'status': 'verified', 'release': value['release'], 'strategy_sha': COMMIT,
            'manifest_sha256': hashlib.sha256(raw).hexdigest(), 'source_sha256': value['files'],
            'support_sha256': value['support_files'], 'verified_at': datetime.now(timezone.utc).isoformat()}


def test_exact_release_proof_keeps_both_new_modules_and_all_support(tmp_path):
    value = source_proof()
    path = tmp_path / 'proof.json'
    path.write_text(json.dumps(value))
    assert digest.source_provenance(str(path), COMMIT) == value
    assert len(value['source_sha256']) == 31 and len(value['support_sha256']) == 3


@pytest.mark.parametrize('fault', ['missing_module', 'missing_support', 'changed_support', 'changed_manifest'])
def test_renderer_rejects_incomplete_or_changed_release_proof(tmp_path, fault):
    value = source_proof()
    if fault == 'missing_module': value['source_sha256'].pop('ic_im_expiry_evidence_v1.py')
    elif fault == 'missing_support': value.pop('support_sha256')
    elif fault == 'changed_support': value['support_sha256']['requirements-poe-v1.4-server.txt'] = '0' * 64
    else: value['manifest_sha256'] = '0' * 64
    path = tmp_path / 'proof.json'; path.write_text(json.dumps(value))
    with pytest.raises(ValueError): digest.source_provenance(str(path), COMMIT)


@pytest.mark.parametrize('fault', [None, 'source', 'support', 'manifest', 'missing_module'])
def test_source_verifier_checks_committed_inventory_and_every_support_byte(tmp_path, monkeypatch, fault):
    value = json.loads((ROOT / MANIFEST).read_bytes())
    for field in ('files', 'support_files'):
        for name in value[field]:
            raw = ('controlled source ' + name).encode()
            path = tmp_path / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
            value[field][name] = hashlib.sha256(raw).hexdigest()
    if fault == 'missing_module': value['files'].pop('ic_im_state_council_calendar_v1.py')
    raw = json.dumps(value).encode()
    manifest_path = tmp_path / MANIFEST
    manifest_path.parent.mkdir(parents=True, exist_ok=True); manifest_path.write_bytes(raw)
    def git(args, **kwargs):
        if args[-2:] == ['rev-parse', 'HEAD']: return COMMIT + '\n'
        if 'ls-tree' in args: return MANIFEST + '\n'
        assert args[-2:] == ['show', COMMIT + ':' + MANIFEST]
        return raw
    monkeypatch.setattr(verifier.subprocess, 'check_output', git)
    if fault == 'source': (tmp_path / 'ic_im_expiry_evidence_v1.py').write_bytes(b'changed')
    elif fault == 'support': (tmp_path / 'data/exchange_calendar/state_council_2027_v1.json').write_bytes(b'changed')
    elif fault == 'manifest': manifest_path.write_bytes(raw + b' ')
    if fault is None:
        proof = verifier.verify(tmp_path, COMMIT)
        assert proof['support_sha256'] == value['support_files']
    else:
        with pytest.raises(ValueError): verifier.verify(tmp_path, COMMIT)


def payload(policy_day='2026-11-12'):
    evidence = {'policy_revision': calendar.REVISION, 'exchange_notice_verified': False,
                'sources': [calendar.load().evidence()]}
    return {'market_date': policy_day, 'next_trade_day': '2027-01-04',
            'signals': {p: {'calendar_source_evidence': deepcopy(evidence)} for p in ('IC', 'IM')}}


@contract.with_calendar_policy(verify_source_metadata=True)
def consume(value):
    return contract.is_session(date(2027, 1, 4))


def test_approved_calendar_is_scoped_and_does_not_claim_exchange_confirmation():
    before = contract._CALENDAR_POLICY_DAY.get()
    assert consume(payload())
    assert contract._CALENDAR_POLICY_DAY.get() is before
    with pytest.raises(ValueError): contract.is_session(date(2027, 1, 4))
    assert contract.CALENDAR_YEARS == frozenset({2022, 2023, 2024, 2025, 2026})
    assert not contract.is_session(date(2024, 2, 9))


@pytest.mark.parametrize('fault', ['before_adoption', 'missing', 'confirmed', 'numeric_flag',
                                  'source_numeric_flag', 'source_hash', 'policy', 'one_product'])
def test_calendar_scope_and_source_metadata_cannot_be_weakened(fault):
    value = payload()
    if fault == 'before_adoption': value['market_date'] = '2026-11-11'
    elif fault == 'one_product': value['signals']['IM'].pop('calendar_source_evidence')
    else:
        for signal in value['signals'].values():
            e = signal['calendar_source_evidence']
            if fault == 'missing': signal.pop('calendar_source_evidence')
            elif fault == 'confirmed': e['exchange_notice_verified'] = True
            elif fault == 'numeric_flag': e['exchange_notice_verified'] = 0
            elif fault == 'source_numeric_flag': e['sources'][0]['exchange_notice_verified'] = 0
            elif fault == 'source_hash': e['sources'][0]['source_text_sha256'] = '0' * 64
            else: e['policy_revision'] = 'wrong'
    before = contract._CALENDAR_POLICY_DAY.get()
    with pytest.raises(ValueError): consume(value)
    assert contract._CALENDAR_POLICY_DAY.get() is before


def test_weekend_makeup_days_remain_closed_and_unknown_years_fail():
    @contract.with_calendar_policy()
    def controls(value):
        cal = calendar.load()
        assert all(not contract.is_session(day) for day in cal.makeup_workdays)
        assert all(not contract.is_session(day) for day in cal.holidays)
        with pytest.raises(ValueError): contract.is_session(date(2028, 1, 4))
    controls({'market_date': '2026-11-12'})


def test_warm_calendar_rechecks_source_bytes_and_restores_scope(tmp_path, monkeypatch):
    value = payload()
    assert consume(value)
    data = tmp_path / 'calendar.json'; data.write_bytes(calendar.DATA_PATH.read_bytes() + b' ')
    monkeypatch.setattr(calendar, 'DATA_PATH', data)
    before = contract._CALENDAR_POLICY_DAY.get()
    with pytest.raises(ValueError): consume(value)
    assert contract._CALENDAR_POLICY_DAY.get() is before


def test_exception_restores_outer_calendar_policy():
    @contract.with_calendar_policy()
    def fail(value):
        assert contract.is_session(date(2027, 1, 4))
        raise RuntimeError('controlled failure')
    outer = date(2026, 11, 11)
    token = contract._CALENDAR_POLICY_DAY.set(outer)
    try:
        with pytest.raises(RuntimeError): fail({'market_date': '2026-11-12'})
        assert contract._CALENDAR_POLICY_DAY.get() is outer
    finally:
        contract._CALENDAR_POLICY_DAY.reset(token)


def test_missing_new_manifest_cannot_downgrade_the_pinned_commit(tmp_path, monkeypatch):
    old = 'docs/ic_im_fear_source_round2_20261109_manifest.json'
    old_raw = (ROOT / old).read_bytes()
    old_path = tmp_path / old
    old_path.parent.mkdir(parents=True, exist_ok=True); old_path.write_bytes(old_raw)
    def git(args, **kwargs):
        if args[-2:] == ['rev-parse', 'HEAD']: return COMMIT + '\n'
        if 'ls-tree' in args: return MANIFEST + '\n' + old + '\n'
        pytest.fail('missing highest pinned manifest must reject before any older proof is read')
    monkeypatch.setattr(verifier.subprocess, 'check_output', git)
    with pytest.raises(ValueError, match='pinned release manifest is missing'):
        verifier.verify(tmp_path, COMMIT)


def test_current_packaging_rejects_older_release_even_with_current_sha(tmp_path):
    old = 'docs/ic_im_fear_source_round2_20261109_manifest.json'
    raw = (ROOT / old).read_bytes(); manifest = json.loads(raw)
    proof = {'status': 'verified', 'release': manifest['release'], 'strategy_sha': COMMIT,
             'manifest_sha256': hashlib.sha256(raw).hexdigest(), 'source_sha256': manifest['files'],
             'verified_at': datetime.now(timezone.utc).isoformat()}
    target = tmp_path / 'old-proof.json'; target.write_text(json.dumps(proof))
    assert digest.source_provenance(str(target), COMMIT) == proof
    with pytest.raises(ValueError, match='execution inventory'):
        digest.source_provenance(str(target), COMMIT, '20261112-expert-review-v5-r2')


def test_both_production_and_readiness_require_the_exact_release():
    for name in ['ic-im-v1-4-daily-digest.yml', 'ic-im-v1-4-delivery-readiness.yml']:
        workflow = (ROOT / '.github/workflows' / name).read_text()
        assert '--expected-source-release 20261112-expert-review-v5-r2' in workflow
        if name == 'ic-im-v1-4-daily-digest.yml':
            line = next(line for line in workflow.splitlines() if '--expected-source-release' in line)
            assert len(line) - len(line.rstrip(chr(92))) == 1
