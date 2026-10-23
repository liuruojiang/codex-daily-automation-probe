"""The new producer proof and mail consumer must select the same audited release."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build_ic_im_v1_4_digest as digest
import verify_icim_source_release as verifier


CURRENT = 'docs/ic_im_daily_release_20261023_repairs_v3_r3_manifest.json'
OLD = 'docs/ic_im_daily_release_20261023_manifest.json'
TEST_COMMIT = 'a' * 40


def proof(relative=CURRENT):
    raw = (ROOT / relative).read_bytes()
    manifest = json.loads(raw)
    return {
        'status': 'verified', 'release': manifest['release'],
        'strategy_sha': TEST_COMMIT,
        'manifest_sha256': hashlib.sha256(raw).hexdigest(),
        'source_sha256': manifest['files'],
        'verified_at': datetime.now(timezone.utc).isoformat(),
    }


def accept(tmp_path, value):
    path = tmp_path / 'proof.json'
    path.write_text(json.dumps(value), encoding='utf-8')
    return digest.source_provenance(str(path), TEST_COMMIT)


def test_current_proof_accepted_without_relabeling_historical_identity(tmp_path):
    value = proof()
    assert accept(tmp_path, value) == value
    assert digest.source_provenance('', '') is None


def test_old_complete_proof_cannot_pass_current_consumer(tmp_path):
    with pytest.raises(ValueError, match='execution inventory'):
        accept(tmp_path, proof(OLD))


@pytest.mark.parametrize('fault', ['commit', 'release', 'manifest', 'missing_runner',
                                  'source_hash', 'future_clock', 'naive_clock'])
def test_current_proof_rejects_mismatched_or_unverified_fields(tmp_path, fault):
    value = deepcopy(proof())
    if fault == 'commit':
        value['strategy_sha'] = 'b' * 40
    elif fault == 'release':
        value['release'] = '20261023-audited-inputs-v1'
    elif fault == 'manifest':
        value['manifest_sha256'] = '0' * 64
    elif fault == 'missing_runner':
        value['source_sha256'].pop('run_ic_im_v1_4_github_digest.py')
    elif fault == 'source_hash':
        value['source_sha256']['poe_ic_im_v1_4_server.py'] = '0' * 64
    elif fault == 'future_clock':
        value['verified_at'] = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    else:
        value['verified_at'] = datetime.now().isoformat()
    with pytest.raises(ValueError):
        accept(tmp_path, value)


def test_verifier_reads_new_committed_manifest_and_rejects_tampered_source(tmp_path, monkeypatch):
    template = json.loads((ROOT / CURRENT).read_bytes())
    for name in template['files']:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = ('# isolated synthetic module ' + name + '\n').encode()
        path.write_bytes(raw)
        template['files'][name] = hashlib.sha256(raw).hexdigest()
    raw = json.dumps(template).encode()
    path = tmp_path / CURRENT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    calls = []

    def git(args, **kwargs):
        calls.append(args)
        if args[-2:] == ['rev-parse', 'HEAD']:
            return TEST_COMMIT + '\n'
        assert args[-2:] == ['show', TEST_COMMIT + ':' + CURRENT]
        return raw

    monkeypatch.setattr(verifier.subprocess, 'check_output', git)
    value = verifier.verify(tmp_path, TEST_COMMIT)
    assert value['release'] == '20261023-redteam-repairs-v3-r3'
    assert len(value['source_sha256']) == 29
    (tmp_path / 'poe_ic_im_v1_4_server.py').write_bytes(b'# changed\n')
    with pytest.raises(ValueError, match='source fingerprint mismatch'):
        verifier.verify(tmp_path, TEST_COMMIT)
    assert all(OLD not in arg for call in calls for arg in call)
