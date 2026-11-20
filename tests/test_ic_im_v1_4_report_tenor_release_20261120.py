"""Validate report-only release progression, not a rewrite of old receipts."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build_ic_im_v1_4_digest as digest
import verify_icim_source_release as verifier

NEW = 'docs/ic_im_report_tenor_spreads_20261120_v1_manifest.json'
OLD = 'docs/ic_im_held_put_close_20261113_v2_manifest.json'
RELEASE = '20261120-report-tenor-spreads-v1'
COMMIT = 'a' * 40


def test_release_changes_only_the_two_intended_source_files():
    old, new = (json.loads((ROOT / name).read_bytes()) for name in (OLD, NEW))
    assert new['release'] == RELEASE
    assert new['files'].keys() == old['files'].keys()
    assert {k for k in new['files'] if new['files'][k] != old['files'][k]} == {
        'ic_im_quarter_roll_v1_3.py', 'poe_ic_im_mainline_v1_4_bot.py'}
    for key in ('support_files', 'fixture_sha256', 'daily_gate_deselections',
                'historical_exclusion_registry_sha256', 'daily_gate_outer_timeout_seconds'):
        assert new[key] == old[key]
    assert new['required_local_tests'] == old['required_local_tests'] + [
        'test_icim_tenor_calendar_20261120.py', 'test_icim_tenor_state_20261120.py']


def test_new_proof_binds_exact_inventory_and_rejects_older_proof(tmp_path):
    def proof_for(name):
        raw = (ROOT / name).read_bytes(); manifest = json.loads(raw)
        return {'status': 'verified', 'release': manifest['release'], 'strategy_sha': COMMIT,
                'manifest_sha256': hashlib.sha256(raw).hexdigest(), 'source_sha256': manifest['files'],
                'support_sha256': manifest['support_files'], 'verified_at': datetime.now(timezone.utc).isoformat()}
    path = tmp_path / 'proof.json'
    proof = proof_for(NEW); path.write_text(json.dumps(proof))
    assert digest.source_provenance(str(path), COMMIT, RELEASE) == proof
    for bad in (proof_for(OLD), {**proof, 'manifest_sha256': '0' * 64},
                {**proof, 'source_sha256': {**proof['source_sha256'], 'ic_im_quarter_roll_v1_3.py': '0' * 64}}):
        path.write_text(json.dumps(bad))
        with pytest.raises(ValueError):
            digest.source_provenance(str(path), COMMIT, RELEASE)


def test_new_committed_manifest_cannot_fall_back_to_old_release(tmp_path, monkeypatch):
    old_path = tmp_path / OLD; old_path.parent.mkdir(parents=True)
    old_path.write_bytes((ROOT / OLD).read_bytes())
    def git(args, **kwargs):
        if args[-2:] == ['rev-parse', 'HEAD']:
            return COMMIT + '\n'
        if 'ls-tree' in args:
            return NEW + '\n' + OLD + '\n'
        pytest.fail('new pinned release missing must reject before old release read')
    monkeypatch.setattr(verifier.subprocess, 'check_output', git)
    with pytest.raises(ValueError, match='pinned release manifest is missing'):
        verifier.verify(tmp_path, COMMIT)
