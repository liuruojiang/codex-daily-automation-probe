"""Independent normal-entry and trust-boundary attacks on the one legacy bridge.

All artifact bytes and API calls here are explicit software fixtures. This file
never fetches the real producer cache, contacts a market source or dispatches.
"""
import copy
from datetime import date, datetime
import hashlib
import importlib
import io
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType
from urllib.parse import parse_qs, urlsplit
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import microcap_legacy_source_recovery as legacy
import restore_microcap_rebalance_recovery as normal
import check_microcap_legacy_source_recovery as checker
from prepare_microcap_local import encode
from test_microcap_legacy_source_recovery import originals, network


def invoke_normal(out, *, orphan=False):
    return normal.recover(runtime_sha=legacy.CONSUMER, producer_sha=legacy.PRODUCER,
        not_after='2026-11-13', out=out, token='explicit-software-fixture', orphan_only=orphan)


def no_new_seal(url, token):
    parsed = urlsplit(url)
    assert parsed.path.endswith('/actions/artifacts') and 'name' in parse_qs(parsed.query)
    return {'artifacts': []}


def test_real_normal_entry_adopts_only_source_pair_on_actual_sealed_search_miss(
        originals, tmp_path, monkeypatch):
    reads, downloads = network(monkeypatch, originals)
    monkeypatch.setattr(normal, 'api_json', no_new_seal)
    result = invoke_normal(tmp_path / 'received')
    assert downloads == ['diagnostics','producer'] and any('/attempts/1/jobs?' in url for url in reads)
    assert result['source_run_id'] == legacy.LEGACY_RUN and result['summary']['successful_stock_stages'] == 644
    assert result['summary']['failed_share_stages'] == 256 and result['summary']['notice_status'] == 'failed'
    manifest_path = Path(result['manifest'])
    manifest = json.loads(manifest_path.read_bytes())
    assert manifest['manifest_origin'] == legacy.ORIGIN and manifest['original_source_was_sealed'] is False
    assert manifest['final_strategy_delivery'] is False and manifest['formal_state_archive'] is None
    assert 'sealed_at' not in manifest and manifest['manifest_issuer']['head_sha'] != legacy.LEGACY_HEAD
    assert all(not name.startswith('scoped-consumer/') for name in manifest['files'])
    for name, raw in originals['diagnostic'].items():
        assert (manifest_path.parent / name).read_bytes() == raw


def test_real_orphan_only_entry_never_queries_frozen_legacy_run(tmp_path, monkeypatch):
    monkeypatch.setattr(normal, 'api_json', no_new_seal)
    monkeypatch.setattr(legacy, 'api_json', lambda *a: pytest.fail('Orphan-only must never read legacy source metadata'))
    result = invoke_normal(tmp_path / 'orphan', orphan=True)
    assert result['restored'] is False and not (tmp_path / 'orphan').exists()


def test_sealed_metadata_read_error_does_not_downgrade_to_old_source_pair(tmp_path, monkeypatch):
    def failed(*args): raise ConnectionError('explicit software read failure')
    monkeypatch.setattr(normal, 'api_json', failed)
    monkeypatch.setattr(legacy, 'api_json', lambda *a: pytest.fail('Unknown new state must not downgrade to legacy'))
    with pytest.raises(ConnectionError): invoke_normal(tmp_path / 'unknown')
    assert not (tmp_path / 'unknown').exists()


def test_real_new_sealed_download_and_manifest_win_before_any_legacy_read(tmp_path, monkeypatch):
    source = tmp_path / 'sealed-source'
    source.mkdir()
    plan = dict(schema='microcap_scoped_recovery_plan_v1',consumer_sha=legacy.CONSUMER,
        delivery_date=legacy.TARGET,required_rebalance_dates=[legacy.TARGET])
    (source / 'scoped-recovery-plan.json').write_bytes(encode(plan))
    normal.seal(root=source,runtime_sha=legacy.CONSUMER,producer_sha=legacy.PRODUCER,
        plan=source/'scoped-recovery-plan.json',out=source/normal.MANIFESTS['boundaries'],
        phase='boundaries',run_id=991,run_attempt=1,producer_commit='f'*40)
    output = io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in source.rglob('*'):
            if path.is_file(): archive.writestr(path.relative_to(source).as_posix(),path.read_bytes())
    body = output.getvalue()
    base = 'https://api.github.com/repos/' + normal.REPOSITORY + '/actions'
    artifact = dict(id=992,name=normal.PREFIXES['boundaries']+legacy.CONSUMER,
        expired=False,created_at='2026-11-12T09:00:00Z',size_in_bytes=len(body),
        digest='sha256:'+hashlib.sha256(body).hexdigest(),archive_download_url=base+'/artifacts/992/zip',
        workflow_run=dict(id=991,head_sha='f'*40))
    def api(url, token):
        parsed=urlsplit(url); query=parse_qs(parsed.query)
        if parsed.path.endswith('/artifacts'):
            return dict(artifacts=[artifact] if query['name'][0]==artifact['name'] else [])
        if parsed.path.endswith('/runs/991'):
            return dict(id=991,head_branch='main',head_sha='f'*40,event='workflow_dispatch',
                path=normal.WORKFLOW,status='completed',conclusion='failure',run_attempt=1)
        if parsed.path.endswith('/runs/991/jobs'):
            return dict(jobs=[dict(steps=[dict(name=normal.UPLOAD_STEPS['boundaries'],status='completed',conclusion='success')])])
        pytest.fail('Unexpected normal sealed API '+url)
    calls=[]
    def download(request, path, *, expected_sha, expected_bytes):
        assert expected_sha==hashlib.sha256(body).hexdigest() and expected_bytes==len(body)
        calls.append(request.full_url);Path(path).write_bytes(body)
        return dict(sha256=expected_sha,bytes=expected_bytes)
    monkeypatch.setattr(normal,'api_json',api)
    monkeypatch.setattr(normal,'stream_download',download)
    monkeypatch.setattr(legacy,'api_json',lambda *a:pytest.fail('Existing authenticated new seal forbids legacy downgrade'))
    result=invoke_normal(tmp_path/'new-reader')
    assert result['source_run_id']==991 and result['artifact_id']==992
    assert result['status']=='scoped_partial_recovery_downloaded_verified_not_adopted'
    assert len(calls)==1 and json.loads(Path(result['manifest']).read_bytes()).get('manifest_origin') is None


@pytest.mark.parametrize('change', [dict(id=1),dict(head_sha='a'*40),dict(run_attempt=2),
    dict(path='.github/workflows/microcap-member-daily.yml'),dict(repository={'full_name':'fork/other'})])
def test_frozen_run_metadata_cannot_be_rebound_on_real_normal_fallback(
        originals,tmp_path,monkeypatch,change):
    _,downloads=network(monkeypatch,originals,change_run=change)
    monkeypatch.setattr(normal,'api_json',no_new_seal)
    with pytest.raises(legacy.LegacyRecoveryError,match='frozen_source_run'):
        invoke_normal(tmp_path/'refused')
    assert downloads==[]


@pytest.mark.parametrize('role', ['diagnostics','producer'])
def test_other_same_named_artifact_id_is_refused_before_either_archive_download(
        originals,tmp_path,monkeypatch,role):
    _,downloads=network(monkeypatch,originals,change_artifact=(role,dict(id=123)))
    monkeypatch.setattr(normal,'api_json',no_new_seal)
    with pytest.raises(legacy.LegacyRecoveryError,match='artifact_identity'):
        invoke_normal(tmp_path/'refused')
    assert downloads==[]


@pytest.mark.parametrize('role', ['diagnostics','producer'])
def test_expired_frozen_pair_is_a_miss_without_partial_adoption(
        originals,tmp_path,monkeypatch,role):
    _,downloads=network(monkeypatch,originals,change_artifact=(role,dict(expired=True)))
    monkeypatch.setattr(normal,'api_json',no_new_seal)
    result=invoke_normal(tmp_path/'absent')
    assert result['restored'] is False and downloads==[] and not (tmp_path/'absent').exists()


@pytest.mark.parametrize('fault', ['fake_seal','fake_formal','consumer_role','successful_notice','successful_share'])
def test_rehashed_source_artifact_cannot_claim_a_finished_or_formal_stage(
        originals,tmp_path,monkeypatch,fault):
    if fault in ('fake_seal','fake_formal','consumer_role'):
        name={'fake_seal':'scoped-recovery-manifest.json','fake_formal':'scoped-delivery/formal-state.zip',
              'consumer_role':'scoped-consumer/outputs/top100_delivery_manifest.json'}[fault]
        originals['diagnostic'][name]=encode(dict(status='complete',ok=True))
    else:
        key='notice' if fault=='successful_notice' else '000045-share'
        name=f'scoped-source-state/runs/{legacy.TARGET}/{key}.json'
        row=json.loads(originals['diagnostic'][name])
        row.update(status='complete',files=json.loads(originals['diagnostic'][
            f'scoped-source-state/runs/{legacy.TARGET}/000001-price.json'])['files'])
        originals['diagnostic'][name]=encode(row)
    network(monkeypatch,originals)
    monkeypatch.setattr(normal,'api_json',no_new_seal)
    with pytest.raises((legacy.LegacyRecoveryError,normal.RecoveryError)):
        invoke_normal(tmp_path/'refused')
    assert not (tmp_path/'refused/scoped-recovery-manifest.json').exists()


def test_offline_native_proof_guard_blocks_child_process_before_any_execution():
    """The original P2 calendar obtains AkShare data in a new Python process."""
    with checker.no_network():
        with pytest.raises(RuntimeError,match='forbids market and HTTP'):
            subprocess.run([sys.executable,'-c','raise AssertionError("must not execute")'],check=True)


def test_offline_native_proof_guard_blocks_socket_and_restores_every_hook():
    import socket
    import urllib.request
    originals=(socket.socket.connect,socket.socket.connect_ex,urllib.request.urlopen,subprocess.Popen)
    with pytest.raises(ValueError,match='explicit proof failure'):
        with checker.no_network():
            with socket.socket() as sock:
                with pytest.raises(RuntimeError,match='forbids market and HTTP'):
                    sock.connect(('127.0.0.1',1))
            with pytest.raises(RuntimeError,match='forbids market and HTTP'):
                urllib.request.urlopen('https://software.invalid/never-requested')
            raise ValueError('explicit proof failure')
    assert (socket.socket.connect,socket.socket.connect_ex,urllib.request.urlopen,subprocess.Popen)==originals


def software_calendar_transport(monkeypatch,tmp_path):
    """Controller-only fixture, deliberately not evidence of actual P2/HTTP."""
    calendar=ModuleType('scripts.exchange_calendar')
    source=tmp_path/'software-calendar-transport.py'
    source.write_bytes(b'# Explicit software transport fixture, never a native cloud result\n')
    calendar.__file__=str(source)
    calendar.SOURCE='explicit-software-transport-unit-fixture'
    sessions=tuple(date.fromisoformat(day) for day in ('2026-11-11','2026-11-12','2026-11-13'))
    calls=[]
    def acquire(day):
        assert day==date.fromisoformat(legacy.TARGET)
        calls.append(day)
        return sessions
    calendar.sessions_for_day=acquire
    calendar.before_network_request=lambda callback:callback()
    monkeypatch.setitem(sys.modules,'scripts.exchange_calendar',calendar)
    parent=importlib.import_module('scripts')
    monkeypatch.setattr(parent,'exchange_calendar',calendar,raising=False)
    return calendar,sessions,calls,acquire


def test_calendar_readback_preserves_returned_tuple_hash_clock_and_bounded_offline_reads(
        monkeypatch,tmp_path,record_property):
    calendar,sessions,calls,acquire=software_calendar_transport(monkeypatch,tmp_path)
    with checker.real_calendar_readback(tmp_path) as proof:
        with checker.no_network():
            assert calendar.sessions_for_day(date(2026,11,12)) is sessions
            assert calendar.sessions_for_day(date(2026,11,13)) is sessions
            with pytest.raises(ValueError,match='does not cover'):
                calendar.sessions_for_day(date(2026,11,14))
        assert proof['sessions']==[day.isoformat() for day in sessions]
        assert proof['sessions_sha256']==hashlib.sha256(encode(proof['sessions'])).hexdigest()
        assert proof['native_calendar_source_sha256']==hashlib.sha256(Path(calendar.__file__).read_bytes()).hexdigest()
        assert datetime.fromisoformat(proof['started_at']).tzinfo is not None
        assert datetime.fromisoformat(proof['observed_at'])>=datetime.fromisoformat(proof['started_at'])
        assert len(calls)==1
        assert json.loads((tmp_path/'independent-calendar.json').read_bytes())==proof
    assert calendar.sessions_for_day is acquire
    record_property('calendar_test_scope','controller software transport fixture only; actual P2 calendar/644 cloud readback not executed')


def test_calendar_readback_restores_original_function_after_a_proof_failure(monkeypatch,tmp_path):
    calendar,sessions,calls,acquire=software_calendar_transport(monkeypatch,tmp_path)
    with pytest.raises(ValueError,match='explicit downstream failure'):
        with checker.real_calendar_readback(tmp_path):
            def expired():raise ValueError('explicit downstream failure')
            calendar.sessions_for_day(date(2026,11,12),before_request=expired)
    assert calendar.sessions_for_day is acquire and len(calls)==1
