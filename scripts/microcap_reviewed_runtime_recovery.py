"""Restore an unchanged approved archive before an exact source-only cutover.

The old native reader restores its own archive.  The existing reviewed
activator then changes only the declared Git source delta.  This is a baseline
for a new formal refresh, never a certificate for the new runtime's old date.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

from activate_microcap_scoped_runtime import activate, checked_path, DATA_PREFIXES
from microcap_scoped_whole_recovery import (recover, fetch_latest, RecoveryError,
    restore_bundle, file_sha, validate_seed, REPOSITORY, MANIFEST)
from prepare_microcap_local import writer_lock, atomic, encode


def git(root, *args, input=None):
    return subprocess.check_output(['git', '-C', str(root), *args], input=input)


def source_review(root, config, runtime_sha, *, baseline=False, fetch=True):
    raw = Path(config).read_bytes()
    value = json.loads(raw)
    if (not isinstance(value, dict)
            or set(value) != {'schema_version', 'baseline_sha', 'runtime_sha', 'changes'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['runtime_sha'] != runtime_sha
            or any(re.fullmatch('[0-9a-f]{40}', str(value[k])) is None
                   for k in ('baseline_sha', 'runtime_sha'))
            or value['baseline_sha'] == runtime_sha):
        raise RecoveryError('reviewed_restore_exact_source_review_required')
    old = value['baseline_sha']
    changes = value['changes']
    if (not isinstance(changes, dict) or not changes
            or len({n.casefold() for n in changes}) != len(changes)):
        raise RecoveryError('reviewed_restore_source_changes_required')
    active = old if baseline else runtime_sha
    if git(root, 'rev-parse', 'HEAD').decode().strip() != active:
        raise RecoveryError('reviewed_restore_receiver_checkout_differs')
    if fetch:
        subprocess.run(['git', '-C', str(root), 'fetch', '--no-tags', 'origin', old], check=True)
    changed = {n.decode('utf-8') for n in git(root, 'diff', '--no-renames', '--name-only',
                                           '-z', old, runtime_sha).split(b'\0') if n}
    if changed != set(changes):
        raise RecoveryError('reviewed_restore_unreviewed_source_delta')
    for name, pair in changes.items():
        path = checked_path(root, name)
        if (name.casefold().startswith(DATA_PREFIXES)
                or name.casefold() in ('outputs', '.microcap_index_cache')
                or not isinstance(pair, list) or len(pair) != 2 or pair[0] == pair[1]
                or re.fullmatch('[0-9a-f]{64}', str(pair[1])) is None
                or (pair[0] is not None and re.fullmatch('[0-9a-f]{64}', str(pair[0])) is None)):
            raise RecoveryError('reviewed_restore_source_delta_changes_data')
        for revision, expected in zip((old, runtime_sha), pair):
            tree = git(root, 'ls-tree', '-z', revision, '--', name)
            if expected is None:
                if tree:
                    raise RecoveryError('reviewed_restore_added_source_already_exists')
                if revision == active and path.exists():
                    raise RecoveryError('reviewed_restore_source_has_local_edits')
                continue
            records = tree.rstrip(b'\0').split(b'\0')
            if len(records) != 1 or b'\t' not in records[0]:
                raise RecoveryError('reviewed_restore_source_is_not_regular_blob')
            metadata, actual = records[0].split(b'\t', 1)
            mode, kind, oid = metadata.split()
            if mode not in (b'100644', b'100755') or kind != b'blob' or actual.decode() != name:
                raise RecoveryError('reviewed_restore_source_is_not_regular_blob')
            blob = git(root, 'cat-file', 'blob', oid.decode())
            if hashlib.sha256(blob).hexdigest() != expected:
                raise RecoveryError('reviewed_restore_source_blob_hash_differs')
            if revision == active:
                working = git(root, 'hash-object', '--path=' + name, '--stdin', input=path.read_bytes()).strip()
                if working != oid:
                    raise RecoveryError('reviewed_restore_source_has_local_edits')
    for record in git(root, 'status', '--porcelain=v1', '-z', '--untracked-files=all').split(b'\0'):
        if record and (b'R' in record[:2] or b'C' in record[:2]
                       or not record[3:].decode('utf-8').startswith(DATA_PREFIXES)):
            raise RecoveryError('reviewed_restore_checkout_has_unreviewed_edits')
    return value, raw


def data_identity(root):
    """Close all restored data, including ignored cache leaves, before cutover."""
    digest = hashlib.sha256(); count = size = 0
    def unreadable(exc):
        raise RecoveryError('reviewed_restore_data_inventory_unreadable') from exc
    for prefix in sorted(DATA_PREFIXES):
        directory = root / prefix.rstrip('/')
        if not directory.exists():
            continue
        checked_path(root, prefix.rstrip('/'))
        if not directory.is_dir():
            raise RecoveryError('reviewed_restore_data_is_not_directory')
        for parent, dirs, files in os.walk(directory, followlinks=False, onerror=unreadable):
            dirs.sort()
            for name in dirs:
                checked_path(root, (Path(parent) / name).relative_to(root).as_posix())
            for name in sorted(files):
                relative = (Path(parent) / name).relative_to(root).as_posix()
                path = checked_path(root, relative)
                if not path.is_file():
                    raise RecoveryError('reviewed_restore_data_is_not_regular')
                length = path.stat().st_size
                digest.update(encode([relative, length, file_sha(path)]))
                count += 1; size += length
                if count > 1000000:
                    raise RecoveryError('reviewed_restore_data_inventory_exceeds_limit')
    return dict(files=count, bytes=size, sha256=digest.hexdigest())


def changed_data(root):
    result = {}
    for record in git(root, 'status', '--porcelain=v1', '-z', '--untracked-files=all').split(b'\0'):
        if not record:
            continue
        name = record[3:].decode('utf-8')
        if b'R' in record[:2] or b'C' in record[:2] or not name.startswith(DATA_PREFIXES):
            raise RecoveryError('reviewed_restore_checkout_has_unreviewed_edits')
        path = checked_path(root, name)
        result[name] = file_sha(path) if path.exists() else None
    return result


def immutable(path, raw):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != raw:
            raise RecoveryError('reviewed_restore_evidence_bytes_differ')
        return
    with path.open('xb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())


def expected_activation(value, review_raw, receipt_raw, restored, root, bundle):
    result = dict(schema='reviewed_scoped_runtime_v1', baseline_sha=value['baseline_sha'],
        runtime_sha=value['runtime_sha'], changes=value['changes'],
        review_sha256=hashlib.sha256(review_raw).hexdigest(),
        restore_receipt_sha256=hashlib.sha256(receipt_raw).hexdigest(),
        preserved_changed_data_files=changed_data(root), activated=True,
        current_data_verified=False, formal_release_verified=False, historical_rewrite_authorized=False)
    if restored.get('status') == 'scoped_whole_restore_verified':
        result['original_archive'] = dict(source_manifest_sha256=restored['source_manifest_sha256'],
            restored_manifest_sha256=file_sha(root / MANIFEST), bundle_sha256=file_sha(bundle),
            seed_date=restored['seed_date'])
    return result


def recover_reviewed(*, root, runtime_sha, not_after, bundle, receipt, token,
                     review, activation_receipt, fallback_url=None, fallback_sha256=None,
                     require_artifact=False, allow_activation=False):
    root, bundle, receipt, review, activation_receipt = map(lambda p: Path(p).resolve(),
        (root, bundle, receipt, review, activation_receipt))
    intent = activation_receipt.with_name(activation_receipt.name + '.intent.json')
    # A new runtime's real published archive always wins.  The old reader is
    # used only after a complete artifact miss, before a new formal refresh.
    current = None if intent.exists() and allow_activation and not require_artifact else fetch_latest(token, runtime_sha)
    if current is not None or require_artifact or not allow_activation:
        return recover(root=root, runtime_sha=runtime_sha, not_after=not_after,
                       bundle=bundle, receipt=receipt, token=token, require_artifact=True)
    if not fallback_url or not fallback_sha256:
        raise RecoveryError('reviewed_restore_original_seed_required')
    if date.fromisoformat(not_after).isoformat() != not_after:
        raise RecoveryError('reviewed_restore_exact_target_date_required')
    activation_receipt = Path(activation_receipt).resolve()
    if activation_receipt.is_relative_to(root) or any(p == root or p.is_relative_to(root)
            for p in (bundle, receipt)) or len({bundle, receipt, review, activation_receipt, intent}) != 5:
        raise RecoveryError('reviewed_restore_activation_receipt_must_be_new_and_external')
    validate_seed(fallback_url, fallback_sha256)
    lock = Path(git(root, 'rev-parse', '--git-path', 'codex-reviewed-restore.lock').decode().strip())
    if not lock.is_absolute():
        lock = root / lock
    with writer_lock(lock):
        original_review = review.read_bytes(); configured = json.loads(original_review)
        owner = Path(git(root,'rev-parse','--git-path','codex-reviewed-restore.owner').decode().strip())
        if not owner.is_absolute():owner = root / owner
        if intent.exists() and not owner.exists():
            raise RecoveryError('reviewed_restore_intent_belongs_to_another_checkout')
        if not owner.exists():immutable(owner,uuid.uuid4().hex.encode())
        owner_raw = owner.read_bytes()
        if re.fullmatch(b'[0-9a-f]{32}',owner_raw) is None:
            raise RecoveryError('reviewed_restore_checkout_owner_invalid')
        binding = dict(root=str(root), runtime_sha=runtime_sha, baseline_sha=configured.get('baseline_sha'),
            target=not_after, bundle=str(bundle), receipt=str(receipt), review=str(review),
            review_sha256=hashlib.sha256(original_review).hexdigest(),
            activation_receipt=str(activation_receipt), seed_url=fallback_url, seed_sha256=fallback_sha256,
            checkout_owner=owner_raw.decode())
        resumed = intent.exists()
        if resumed:
            journal_raw = intent.read_bytes(); journal = json.loads(journal_raw)
            if (journal.get('schema') != 'reviewed_runtime_restore_intent_v1'
                    or journal.get('binding') != binding or not isinstance(journal.get('attempts'), list)
                    or journal.get('phase') not in ('restore_intent','baseline_verified','activation_intent','complete')):
                raise RecoveryError('reviewed_restore_intent_identity_differs')
        else:
            if activation_receipt.exists() or bundle.exists() or receipt.exists():
                raise RecoveryError('reviewed_restore_preexisting_evidence_has_no_intent')
            source_review(root, review, runtime_sha)
            journal = dict(schema='reviewed_runtime_restore_intent_v1', binding=binding,
                created_at=datetime.now(timezone.utc).isoformat(), phase='restore_intent', attempts=[])
            journal_raw = encode(journal); immutable(intent, journal_raw)

        def save():
            nonlocal journal_raw
            if intent.read_bytes() != journal_raw or review.read_bytes() != original_review:
                raise RecoveryError('reviewed_restore_intent_or_review_changed')
            journal_raw = encode(journal); atomic(intent, journal_raw)

        old = binding['baseline_sha']; head = git(root,'rev-parse','HEAD').decode().strip()
        if head not in (old, runtime_sha):
            raise RecoveryError('reviewed_restore_receiver_checkout_differs')
        value, _ = source_review(root, review, runtime_sha, baseline=head == old, fetch=False)
        for number, attempt in enumerate(journal['attempts'], 1):
            expected = activation_receipt.with_name(activation_receipt.name + f'.restore-{number}.json')
            if attempt.get('receipt') != str(expected):
                raise RecoveryError('reviewed_restore_attempt_path_differs')
            if attempt.get('receipt_sha256') and file_sha(expected) != attempt['receipt_sha256']:
                raise RecoveryError('reviewed_restore_original_attempt_changed')
        new_baseline = False
        if 'baseline' not in journal:
            if journal['phase'] != 'restore_intent' or activation_receipt.exists() or receipt.exists():
                raise RecoveryError('reviewed_restore_unknown_partial_state')
            if head == runtime_sha:
                subprocess.run(['git','-C',str(root),'checkout','--detach',old],check=True)
            if len(journal['attempts']) >= 2:
                raise RecoveryError('reviewed_restore_attempt_budget_exhausted')
            number = len(journal['attempts']) + 1
            attempt_receipt = activation_receipt.with_name(activation_receipt.name + f'.restore-{number}.json')
            if attempt_receipt.exists():
                raise RecoveryError('reviewed_restore_unknown_attempt_evidence')
            attempt = dict(receipt=str(attempt_receipt), started_at=datetime.now(timezone.utc).isoformat())
            journal['attempts'].append(attempt); save()
            try:
                if bundle.exists():
                    # Only the explicitly pinned original seed can be adopted
                    # after an unclosed download/restore. Never guess an
                    # unjournaled artifact identity or replace unknown bytes.
                    if file_sha(bundle) != fallback_sha256:
                        raise RecoveryError('reviewed_restore_unclosed_bundle_is_not_pinned_seed')
                    work = activation_receipt.parent / (activation_receipt.name + f'.native-recheck-{number}')
                    work.mkdir()
                    restored = dict(restore_bundle(root,bundle,old,not_after,work), repository=REPOSITORY,
                        status='scoped_whole_restore_verified', source='reused_exact_original_seed',
                        seed_url=fallback_url, seed_sha256=fallback_sha256,
                        recovery_reason='original_attempt_preserved_native_restore_rechecked')
                    immutable(attempt_receipt, encode(restored))
                else:
                    restored = recover(root=root,runtime_sha=old,not_after=not_after,
                        bundle=bundle,receipt=attempt_receipt,token=token,
                        fallback_url=fallback_url,fallback_sha256=fallback_sha256)
                raw = attempt_receipt.read_bytes()
                if (json.loads(raw) != restored or restored.get('restored') is not True
                        or restored.get('runtime_sha') != old):
                    raise RecoveryError('reviewed_restore_baseline_delegate_identity_differs')
            except Exception:
                if attempt_receipt.exists():
                    attempt['receipt_sha256'] = file_sha(attempt_receipt)
                attempt['state'] = 'failed'; save()
                raise
            attempt.update(state='verified', receipt_sha256=hashlib.sha256(raw).hexdigest())
            journal['baseline'] = dict(source_receipt=str(attempt_receipt), receipt_sha256=attempt['receipt_sha256'],
                bundle_sha256=file_sha(bundle), data=data_identity(root))
            journal['phase'] = 'baseline_verified'; save()
            new_baseline = True

        baseline = journal['baseline']; source_receipt = Path(baseline['source_receipt'])
        if source_receipt not in {Path(a['receipt']) for a in journal['attempts']}:
            raise RecoveryError('reviewed_restore_baseline_receipt_role_differs')
        raw = source_receipt.read_bytes(); restored = json.loads(raw)
        if (hashlib.sha256(raw).hexdigest() != baseline['receipt_sha256']
                or file_sha(bundle) != baseline['bundle_sha256']
                or (not new_baseline and data_identity(root) != baseline['data'])
                or restored.get('restored') is not True or restored.get('runtime_sha') != old):
            raise RecoveryError('reviewed_restore_verified_baseline_changed')
        immutable(receipt, raw)  # Exact original receipt bytes, not a re-signed seed.
        head = git(root,'rev-parse','HEAD').decode().strip()
        if 'activation' not in journal:
            if head != old or journal['phase'] != 'baseline_verified' or activation_receipt.exists():
                raise RecoveryError('reviewed_restore_unknown_activation_state')
            journal['activation'] = expected_activation(value,original_review,raw,restored,root,bundle)
            journal['phase'] = 'activation_intent'; save()
        expected = journal['activation']
        if head == old:
            if activation_receipt.exists() or journal['phase'] != 'activation_intent':
                raise RecoveryError('reviewed_restore_unknown_activation_state')
            activate(root,review,receipt,activation_receipt,bundle=bundle)
        # Independently read back the actual new code and every restored data
        # byte, including ignored files. A lost response never replays checkout.
        source_review(root,review,runtime_sha,fetch=False)
        if data_identity(root) != baseline['data'] or review.read_bytes() != original_review or receipt.read_bytes() != raw:
            raise RecoveryError('reviewed_restore_activation_readback_changed')
        if expected != expected_activation(value,original_review,raw,restored,root,bundle):
            raise RecoveryError('reviewed_restore_activation_intent_fields_differ')
        reconciled = dict(expected,schema='reviewed_scoped_runtime_readback_v1',
            reconciliation='new_source_and_original_data_reverified_after_lost_activation_receipt')
        if not activation_receipt.exists():
            if journal.get('activation_receipt_sha256') is not None:
                raise RecoveryError('reviewed_restore_previously_closed_activation_receipt_missing')
            immutable(activation_receipt,encode(reconciled))
        activation_raw = activation_receipt.read_bytes(); activated = json.loads(activation_raw)
        if (activated not in (expected,reconciled)
                or (journal.get('activation_receipt_sha256') is not None
                    and journal['activation_receipt_sha256'] != hashlib.sha256(activation_raw).hexdigest())):
            raise RecoveryError('reviewed_restore_activation_receipt_differs')
        if journal['phase'] != 'complete':
            journal.update(phase='complete', activation_receipt_sha256=hashlib.sha256(activation_raw).hexdigest()); save()
        # Keep both original documents untouched and explicitly distinct.
        if (activated.get('runtime_sha') != runtime_sha or not activated.get('activated')
                or restored.get('runtime_sha') != value['baseline_sha']):
            raise RecoveryError('reviewed_restore_source_activation_did_not_complete')
        return dict(status='approved_baseline_restored_then_exact_source_activated',
                    baseline_restore=restored, source_activation=activated,
                    baseline_receipt=str(Path(receipt).resolve()),
                    activation_receipt=str(activation_receipt),
                    recovery_intent=str(intent),
                    current_data_verified=False, formal_release_verified=False,
                    historical_data_rewritten=False, email_sent=False, orders_placed=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('root', 'bundle', 'receipt', 'review', 'activation-receipt'):
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ('runtime-sha', 'not-after-date'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--fallback-url')
    parser.add_argument('--fallback-sha256')
    parser.add_argument('--require-artifact', action='store_true')
    parser.add_argument('--allow-activation', action='store_true')
    args = parser.parse_args()
    report = recover_reviewed(root=args.root, runtime_sha=args.runtime_sha,
        not_after=args.not_after_date, bundle=args.bundle, receipt=args.receipt,
        token=os.environ.get('GITHUB_TOKEN', ''), review=args.review,
        activation_receipt=args.activation_receipt, fallback_url=args.fallback_url,
        fallback_sha256=args.fallback_sha256, require_artifact=args.require_artifact,
        allow_activation=args.allow_activation)
    print(json.dumps(report))


if __name__ == '__main__':
    main()
