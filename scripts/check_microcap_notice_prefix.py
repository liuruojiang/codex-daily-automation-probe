"""Cold, source-only acceptance of the approved notice prefix relay.

The real whole archive, bootstrap and two original Native receipts are used.
This never earns current freshness, formal delivery, a daily email or an order.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import socket
import urllib.request

from microcap_notice_prefix import (
    CONSUMER, PRODUCER, START, END, POINTER, FRESHNESS, RECEIPTS,
    inspect_bundle, load_config, sha_file, stream_download, warm,
)
from prepare_microcap_local import atomic, encode


@contextmanager
def no_source_http():
    def forbidden(*args, **kwargs):
        raise RuntimeError('Notice acceptance permits no source HTTP request')
    original = (socket.socket.connect, socket.socket.connect_ex, urllib.request.urlopen)
    socket.socket.connect = socket.socket.connect_ex = urllib.request.urlopen = forbidden
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex, urllib.request.urlopen = original


def protected_snapshot(root):
    manifest_name = 'outputs/top100_delivery_manifest.json'
    manifest = json.loads((root / manifest_name).read_bytes())
    if (manifest.get('expected_date') != '2026-11-10'
            or manifest.get('status') != 'complete' or manifest.get('ok') is not True):
        raise ValueError('Cold acceptance requires the original complete D10 whole archive')
    names = {manifest_name, POINTER, FRESHNESS,
             *manifest['inputs'], *manifest['artifacts']}
    result = {}
    for name in sorted(names):
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise ValueError('Protected archive object escapes its root')
        result[name] = sha_file(path) if path.is_file() else None
    return result


def check(*, consumer, producer, state, pool, config, restoration, target, work, out):
    consumer, producer, state, pool, config, restoration, work, out = map(
        lambda p: Path(p).resolve(),
        (consumer, producer, state, pool, config, restoration, work, out))
    source = json.loads(restoration.read_bytes())
    if (source.get('status') != 'scoped_whole_restore_verified'
            or source.get('source') != 'published_scoped_whole_artifact'
            or source.get('fallback_used') is not False
            or source.get('runtime_sha') != CONSUMER or source.get('seed_date') != '2026-11-10'):
        raise ValueError('Cold acceptance requires a real artifact hit without seed fallback')
    if work.exists() or out.exists():
        raise ValueError('Preserve existing notice cold-acceptance evidence')
    work.mkdir(parents=True)
    before = protected_snapshot(consumer)
    if json.loads((producer / POINTER).read_bytes()) != dict(manifest_path=START[0], manifest_sha256=START[1]):
        raise ValueError('Private cold producer does not start at the real D10 authority')
    anchors = {path: path.read_bytes() for path in
               (state / 'bootstrap.json', state / 'reference-proxy.csv')}
    bootstrap = json.loads(anchors[state / 'bootstrap.json'])
    if (bootstrap.get('status') != 'complete' or len(bootstrap.get('initialized_symbols', [])) != 300
            or bootstrap.get('failures')):
        raise ValueError('Cold prefix acceptance requires the actual complete 300-stock bootstrap')
    item = load_config(config)
    if item is None:
        raise ValueError('Approved notice release pointer is absent')
    relay = work / 'relay'
    relay.mkdir()
    bundle = relay / 'notice-prefix.zip'
    # This is a transport read from the already published, SHA-bound release.
    # Every later Native validation runs with source HTTP disabled.
    stream_download(urllib.request.Request(item['url']), bundle, expected_sha=item['sha256'])
    manifest = inspect_bundle(bundle=bundle, expected_sha=item['sha256'], not_after=target)
    common = dict(config=config, root=producer, protected_root=consumer, state=state,
                  pool=pool, target=target, out=relay)
    with no_source_http():
        first = warm(**common)
        atomic(work / 'first-admission.json', encode(first))
        if (first.get('status') != 'approved_notice_prefix_imported_source_only'
                or first.get('authority') != END or first.get('current_signal_ready') is not False):
            raise ValueError('Actual cold prefix admission failed')
        pair = {name: (producer / name).read_bytes() for name in (POINTER, FRESHNESS)}
        second = warm(**common)
        atomic(work / 'second-admission.json', encode(second))
        if (second.get('status') != 'skipped_notice_prefix_already_covered'
                or any((producer / name).read_bytes() != raw for name, raw in pair.items())):
            raise ValueError('Second native admission did not preserve the already covered pair')
    if any(path.read_bytes() != raw for path, raw in anchors.items()) or protected_snapshot(consumer) != before:
        raise ValueError('Notice relay changed its original consumer or bootstrap anchor')
    original_receipts = []
    for spec in RECEIPTS:
        raw = (producer / spec['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != spec['sha256']:
            raise ValueError('Approved original receipt bytes changed')
        proof = json.loads(raw)
        original_receipts.append(dict(**spec, required_asof=proof['required_asof'],
                                      completed_at=proof['completed_at']))
    report = dict(schema='microcap_notice_prefix_cold_acceptance_v1',
        status='actual_native_prefix_relay_and_idempotent_readback_verified_source_only',
        requested_target=target, prefix_date='2026-11-11',
        consumer_runtime=CONSUMER, producer_runtime=PRODUCER,
        restored_artifact_id=source['artifact_id'], restored_source_run=source['source_run'],
        fallback_used=False, protected_archive_date='2026-11-10',
        protected_objects=len(before), protected_snapshot_sha256=hashlib.sha256(encode(before)).hexdigest(),
        protected_consumer_unchanged=True, bootstrap_and_reference_unchanged=True,
        baseline_files=len(manifest['baseline_files']), payload_files=len(manifest['payload_files']),
        original_receipts=original_receipts, original_clocks_retained=True,
        bundle_sha256=item['sha256'], release_url=item['url'], exporter_commit=item['producer_commit'],
        first_status=first['status'], second_status=second['status'],
        actual_native_verification=True, source_http_disabled_during_verification=True,
        stock_collection_calls=0, formal_refresh_calls=0, SMTP_calls=0,
        current_signal_ready=False, final_strategy_delivery=False, orders_placed=False)
    atomic(work / 'protected-snapshot.json', encode(before))
    atomic(out, encode(report))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('consumer', 'producer', 'state', 'pool', 'config', 'restoration', 'work', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--date', required=True)
    args = parser.parse_args()
    print(json.dumps(check(consumer=args.consumer, producer=args.producer, state=args.state,
        pool=args.pool, config=args.config, restoration=args.restoration, target=args.date,
        work=args.work, out=args.out)))


if __name__ == '__main__':
    main()
