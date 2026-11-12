"""Verify frozen source intake after one explicit real calendar acquisition.

This verifies earned stock inputs only. Failed notice/share stages remain failed;
neither a formal strategy delivery nor a current signal is created here.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import urllib.request

from microcap_legacy_source_recovery import CONSUMER, PRODUCER, TARGET, validate_bridge_manifest
from prepare_microcap_local import atomic, encode
from restore_microcap_rebalance_recovery import admit_resume, sha_file


@contextmanager
def no_network():
    def forbidden(*args, **kwargs):
        raise RuntimeError('Legacy source verification forbids market and HTTP requests')
    original = (socket.socket.connect, socket.socket.connect_ex, urllib.request.urlopen, subprocess.Popen)
    socket.socket.connect = socket.socket.connect_ex = urllib.request.urlopen = forbidden
    subprocess.Popen = forbidden
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex, urllib.request.urlopen, subprocess.Popen = original


@contextmanager
def real_calendar_readback(work):
    """Bind the exact real native result for subsequent offline proof reads."""
    from scripts import exchange_calendar as calendar
    original = calendar.sessions_for_day
    started = datetime.now(timezone.utc).isoformat()
    sessions = original(date.fromisoformat(TARGET))
    raw = encode([value.isoformat() for value in sessions])
    proof = dict(source=calendar.SOURCE, native_runtime=PRODUCER, target=TARGET,
        started_at=started, observed_at=datetime.now(timezone.utc).isoformat(),
        native_calendar_source_sha256=hashlib.sha256(Path(calendar.__file__).read_bytes().replace(b'\r\n', b'\n')).hexdigest(),
        returned_by_actual_native_function=True, software_calendar_used=False,
        native_acquisition_calls=1, sessions=[value.isoformat() for value in sessions],
        sessions_sha256=hashlib.sha256(raw).hexdigest())
    atomic(work/'independent-calendar.json', encode(proof))
    def read(day, *, before_request=None):
        if not isinstance(day, date) or not sessions[0] <= day <= sessions[-1]:
            raise ValueError('Native calendar readback does not cover the requested day')
        if before_request is not None:
            calendar.before_network_request(before_request)
        return sessions
    calendar.sessions_for_day = read
    try:
        yield proof
    finally:
        calendar.sessions_for_day = original


def check(*, manifest_path, consumer, producer, work, out):
    manifest_path, consumer, producer, work, out = map(lambda p: Path(p).resolve(),
        (manifest_path, consumer, producer, work, out))
    value = json.loads(manifest_path.read_bytes())
    validate_bridge_manifest(value)
    if out.exists():
        raise ValueError('Preserve existing legacy intake acceptance receipt')
    work.mkdir(parents=True, exist_ok=False)
    def no_formal_stage(*args, **kwargs):
        raise AssertionError('Source-only intake must never restore or refresh a formal strategy')
    admission = admit_resume(manifest_path, consumer=consumer, producer=producer, state=producer.parent,
        prepare_out=work/'prepare', delivery_out=work/'delivery', target=TARGET,
        consumer_sha=CONSUMER, producer_sha=PRODUCER, run=no_formal_stage)
    if (admission.get('formal_archive_restored') or admission.get('consumer_partial_admitted')
            or admission.get('original_json_and_clocks_rewritten') is not False):
        raise ValueError('Legacy acceptance is confined to original source inputs')
    sys.path.insert(0, str(producer))
    directory = producer.parent/'runs'/TARGET
    result_raw = (directory/'result.json').read_bytes()
    result = json.loads(result_raw)
    context = result['context']
    baseline = json.loads((producer.parent/'bootstrap.json').read_bytes())
    with no_network():
        from scripts.stock_input_contract import source_fingerprint
        if source_fingerprint(producer) != context['native_sources']:
            raise ValueError('Actual legacy input verification requires the exact P2 native sources')
    verified = dict(price=0, share=0, ratio=0)
    bound_files = {}
    with real_calendar_readback(work) as calendar_proof, no_network():
        import maintain_microcap_local as local
        import microcap_top100_mom16_biweekly_live_v2_0 as native
        master = native.base_mod.freq_mod.load_security_master()
        for symbol in result['symbols']:
            for stage in verified:
                record_raw = (directory/(symbol+'-'+stage+'.json')).read_bytes()
                record = json.loads(record_raw)
                if record['status'] != 'complete':
                    continue
                if record.get('context') != context:
                    raise ValueError('Legacy successful input has a different original context')
                if stage == 'price':
                    anchor = local.price_seed_anchor(producer, symbol, baseline)
                    files = local.price_proof(producer, symbol, TARGET, master, anchor)
                elif stage == 'share':
                    files = local.share_proof(producer, symbol, TARGET)
                else:
                    files = local.ratio_proof(producer, symbol, TARGET, master)
                if files != record['files'] or (directory/(symbol+'-'+stage+'.json')).read_bytes() != record_raw:
                    raise ValueError('Native legacy verification changed or contradicted the earned receipt')
                for name, digest in files.items():
                    if name in bound_files and bound_files[name] != digest:
                        raise ValueError('Legacy successful source closures conflict')
                    bound_files[name] = digest
                verified[stage] += 1
    if verified != dict(price=300, share=44, ratio=300) or (directory/'result.json').read_bytes() != result_raw:
        raise ValueError('Legacy native acceptance must preserve all 644 successes and original failures')
    if local.hashes(producer, bound_files) != bound_files:
        raise ValueError('Legacy native input verification changed original source bytes')
    report = dict(schema='legacy_source_native_readback_acceptance_v1',
        status='original_644_source_stages_native_verified_not_formal_delivery',
        manifest_sha256=sha_file(manifest_path), source_run_id=value['source_run_id'],
        producer_commit=value['producer_commit'], original_target=TARGET,
        native_source_runtime=PRODUCER, consumer_runtime=CONSUMER,
        original_json_and_clocks_rewritten=False, actual_private_admission=True,
        verified_stock_stages=verified, successful_stock_stages=sum(verified.values()),
        bound_file_count=len(bound_files), failed_share_stages=256,
        notice_status=result['notice_status'], metadata_status=result['metadata_status'],
        original_result_sha256=hashlib.sha256(result_raw).hexdigest(),
        calendar_native_acquisition_calls=calendar_proof['native_acquisition_calls'],
        calendar_sessions_sha256=calendar_proof['sessions_sha256'],
        calendar_source=calendar_proof['source'], software_calendar_used=False,
        native_verification_subprocesses_allowed=False,
        source_evidence=value['legacy_source_evidence'], market_requests=0,
        formal_refresh_repeated=False, current_signal_ready=False, SMTP_calls=0, orders_placed=False)
    atomic(out, encode(report))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('manifest', 'consumer', 'producer', 'work', 'out'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    result = check(manifest_path=args.manifest, consumer=args.consumer, producer=args.producer,
        work=args.work, out=args.out)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
