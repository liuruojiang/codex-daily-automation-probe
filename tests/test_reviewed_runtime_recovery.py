"""Real Git source cutovers; the tiny restore delegate is explicitly software.

Full native restore remains covered by the actual cloud acceptance, not these
transport-boundary fixtures.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import microcap_reviewed_runtime_recovery as reviewed


@pytest.fixture
def case(tmp_path, monkeypatch):
    root = tmp_path / 'consumer'
    root.mkdir()
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.PIPE).decode().strip()
    git('init', '-q')
    git('config', 'user.name', 'Software transport fixture')
    git('config', 'user.email', 'fixture@example.invalid')
    source = root / 'scripts/parser.py'
    source.parent.mkdir()
    source.write_bytes(b'old parser\n')
    data = root / 'outputs/history.csv'
    data.parent.mkdir()
    data.write_bytes(b'old exact historical bytes\r\n')
    git('add', '.')
    git('commit', '-qm', 'old source')
    old = git('rev-parse', 'HEAD')
    source.write_bytes(b'new date parser\n')
    git('add', 'scripts/parser.py')
    git('commit', '-qm', 'new parser only')
    new = git('rev-parse', 'HEAD')
    git('remote', 'add', 'origin', str(root))
    review = tmp_path / 'review.json'
    review.write_text(json.dumps(dict(schema_version=1, baseline_sha=old, runtime_sha=new,
        changes={'scripts/parser.py': [hashlib.sha256(b'old parser\n').hexdigest(),
                                      hashlib.sha256(b'new date parser\n').hexdigest()]})))
    args = dict(root=root, runtime_sha=new, not_after='2026-11-12', bundle=tmp_path/'whole.zip',
        receipt=tmp_path/'restore.json', token='software-no-network', review=review,
        activation_receipt=tmp_path/'prepare/runtime-activation.json',
        fallback_url='https://github.com/liuruojiang/codex-daily-automation-probe/releases/download/exact/original.zip',
        fallback_sha256='c'*64, allow_activation=True)
    monkeypatch.setattr(reviewed, 'fetch_latest', lambda *args: None)
    calls = []
    def restore(**kw):
        calls.append(kw)
        assert git('rev-parse', 'HEAD') == kw['runtime_sha']
        if kw['runtime_sha'] == old:
            assert source.read_bytes().replace(b'\r\n', b'\n') == b'old parser\n'
        kw['bundle'].write_bytes(b'software baseline fixture')
        value = dict(restored=True, strategy_sha=kw['runtime_sha'], runtime_sha=kw['runtime_sha'])
        kw['receipt'].write_text(json.dumps(value))
        return value
    monkeypatch.setattr(reviewed, 'recover', restore)
    return args, git, source, data, calls


def test_original_reader_restores_before_exact_code_only_activation(case):
    args, git, source, data, calls = case
    original = data.read_bytes()
    result = reviewed.recover_reviewed(**args)
    assert len(calls) == 1
    assert git('rev-parse', 'HEAD') == args['runtime_sha']
    assert source.read_bytes().replace(b'\r\n', b'\n') == b'new date parser\n'
    assert data.read_bytes() == original
    restored = json.loads(args['receipt'].read_bytes())
    assert restored == result['baseline_restore']
    assert restored['runtime_sha'] != args['runtime_sha']
    assert result['source_activation']['activated']
    assert not result['formal_release_verified'] and not result['current_data_verified']
    assert not result['historical_data_rewritten'] and not result['email_sent']


@pytest.mark.parametrize('mode', ['artifact', 'required', 'no_activation'])
def test_new_archive_or_readback_never_uses_old_runtime(case, monkeypatch, mode):
    args, git, source, data, calls = case
    if mode == 'artifact':
        monkeypatch.setattr(reviewed, 'fetch_latest', lambda *args: {'id': 123})
    if mode == 'required':
        args['require_artifact'] = True
    if mode == 'no_activation':
        args['allow_activation'] = False
    reviewed.recover_reviewed(**args)
    assert len(calls) == 1 and calls[0]['runtime_sha'] == args['runtime_sha']
    assert calls[0]['require_artifact'] is True and 'fallback_url' not in calls[0]
    assert not args['activation_receipt'].exists()


@pytest.mark.parametrize('fault', ['hash', 'path_set', 'data_change', 'local_source', 'local_other', 'missing_seed'])
def test_bad_reviews_reject_before_checkout_or_restore(case, fault):
    args, git, source, data, calls = case
    value = json.loads(args['review'].read_bytes())
    if fault == 'hash':
        value['changes']['scripts/parser.py'][1] = '0'*64
    elif fault == 'path_set':
        value['changes']['tests/unreviewed.py'] = [None, '1'*64]
    elif fault == 'data_change':
        value['changes'] = {'outputs/history.csv': ['1'*64, '2'*64]}
    elif fault == 'local_source':
        source.write_bytes(b'unsaved user edit\n')
    elif fault == 'local_other':
        (args['root']/'user.txt').write_text('preserve me')
    elif fault == 'missing_seed':
        args['fallback_sha256'] = None
    args['review'].write_text(json.dumps(value))
    with pytest.raises((ValueError, reviewed.RecoveryError)):
        reviewed.recover_reviewed(**args)
    assert not calls and git('rev-parse', 'HEAD') == args['runtime_sha']
    assert not args['activation_receipt'].exists()
