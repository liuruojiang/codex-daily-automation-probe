"""Isolated next-session delivery faults; never dispatch or contact SMTP."""
from datetime import date, datetime, timezone
from http.client import IncompleteRead
import io
import json
from pathlib import Path
import sys
import urllib.error

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import check_microcap_delivery as gate
import check_microcap_member_send as partial
from test_cloudflare_dispatch_retry import execute
from test_microcap_midnight_delivery_recovery import trusted_receipt, DAY as RECEIPT_DAY, NOW as RECEIPT_NOW
import restore_ic_im_v1_3_ledger as receipt_transport

DAY = '2026-11-10'
NORMAL = gate.delivery_marker_name(date.fromisoformat(DAY), 'close_confirmed')


def run_gate(monkeypatch, names):
    outputs = []
    monkeypatch.setattr(gate, 'now_utc', lambda: datetime(2026, 11, 10, 8, tzinfo=timezone.utc))
    monkeypatch.setattr(gate, 'fetch_artifacts', lambda repo, token, name, api: {
        'artifacts': [dict(id=1, name=name, expired=False)] if name in names else []})
    monkeypatch.setattr(gate, 'write_outputs', outputs.append)
    monkeypatch.setattr(sys, 'argv', ['gate', '--publication-mode', 'close_confirmed',
        '--repository', 'isolated/repo', '--token', 'isolated'])
    gate.main()
    return outputs[0]


def test_previous_day_success_and_intent_do_not_block_tomorrow(monkeypatch):
    old = NORMAL.replace(DAY, '2026-11-09')
    result = run_gate(monkeypatch, {old, old + '-send-intent', old + '-smtp-accepted'})
    assert result['delivery_date'] == DAY
    assert result['marker_name'] == NORMAL and result['should_send'] == 'true'


def test_same_day_completion_suppresses_duplicate_even_with_intent(monkeypatch):
    result = run_gate(monkeypatch, {NORMAL, NORMAL + '-send-intent'})
    assert result['should_send'] == 'false' and result['recover_marker'] == 'false'


def test_same_day_accepted_receipt_recovers_marker_without_resend(monkeypatch):
    calls = []
    monkeypatch.setattr(gate, 'verify_smtp_receipt', lambda *a: calls.append(a))
    result = run_gate(monkeypatch, {NORMAL + '-smtp-accepted', NORMAL + '-send-intent'})
    assert result['should_send'] == 'false' and result['recover_marker'] == 'true'
    assert len(calls) == 1 and calls[0][4] == date.fromisoformat(DAY)


def test_unknown_current_intent_requires_reconciliation(monkeypatch):
    with pytest.raises(RuntimeError, match='uncertain'):
        run_gate(monkeypatch, {NORMAL + '-send-intent'})


def run_partial(tmp_path, monkeypatch, names):
    path = tmp_path / 'metadata.json'
    path.write_text(json.dumps(dict(schema='member_daily_report_v1', status='PARTIAL',
        signal_date=DAY, publication_mode='close_confirmed')))
    outputs = []
    monkeypatch.setenv('GITHUB_REPOSITORY', 'isolated/repo')
    monkeypatch.setenv('GITHUB_TOKEN', 'isolated')
    monkeypatch.setattr(partial, 'fetch_artifacts', lambda repo, token, name: {
        'artifacts': [dict(id=1, name=name, expired=False)] if name in names else []})
    monkeypatch.setattr(partial, 'write_outputs', outputs.append)
    monkeypatch.setattr(sys, 'argv', ['partial', '--metadata', str(path), '--normal-marker', NORMAL])
    partial.main()
    return outputs[0]


def test_partial_has_own_transaction_and_cannot_take_complete_marker(tmp_path, monkeypatch):
    result = run_partial(tmp_path, monkeypatch, set())
    assert result['marker_name'] == NORMAL + '-partial-information'
    assert result['should_send'] == 'true'


def test_duplicate_partial_does_not_resend(tmp_path, monkeypatch):
    result = run_partial(tmp_path, monkeypatch, {NORMAL + '-partial-information'})
    assert result['should_send'] == 'false'


def test_unknown_partial_intent_is_not_bypassed(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError, match='unknown terminal state'):
        run_partial(tmp_path, monkeypatch, {NORMAL + '-partial-information-send-intent'})


def test_tomorrow_sixteen_clock_dispatches_one_normal_member_workflow():
    result = execute(cron='0 8 * * MON-FRI', scheduledTime=1794297600000,
                     memberDaily=True, succeedOn=1)
    assert not result['error'] and len(result['calls']) == 1
    call = result['calls'][0]
    assert call['url'].endswith('/microcap-member-daily.yml/dispatches')
    assert call['body'] == {'ref': 'main', 'inputs': {
        'validation_only': False, 'external_schedule': True, 'publication_mode': 'close_confirmed'}}


def test_transient_marker_lookup_504_retries_without_repeating_send(monkeypatch):
    """Observed 504 can also happen at the gate, before checkpoint restoration."""
    calls = []
    def urlopen(request, **kwargs):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise urllib.error.HTTPError(request.full_url, 504, 'Gateway Time-out', {}, None)
        return io.BytesIO(b'{"artifacts": []}')
    monkeypatch.setattr(gate.urllib.request, 'urlopen', urlopen)
    monkeypatch.setattr(gate, 'sleep', lambda seconds: None, raising=False)
    assert gate.fetch_artifacts('isolated/repo', 'isolated', NORMAL) == {'artifacts': []}
    assert len(calls) == 2 and calls[0] == calls[1]


def test_marker_permission_failure_is_not_retried_or_treated_as_absent(monkeypatch):
    calls = []
    def urlopen(request, **kwargs):
        calls.append(request.full_url)
        raise urllib.error.HTTPError(request.full_url, 403, 'Forbidden', {}, None)
    monkeypatch.setattr(gate.urllib.request, 'urlopen', urlopen)
    with pytest.raises(urllib.error.HTTPError) as exc:
        gate.fetch_artifacts('isolated/repo', 'isolated', NORMAL)
    assert exc.value.code == 403 and len(calls) == 1


def test_exhausted_transient_marker_lookup_remains_blocked(monkeypatch):
    calls = []
    def urlopen(request, **kwargs):
        calls.append(request.full_url)
        raise urllib.error.HTTPError(request.full_url, 504, 'Gateway Time-out', {}, None)
    monkeypatch.setattr(gate.urllib.request, 'urlopen', urlopen)
    monkeypatch.setattr(gate, 'sleep', lambda seconds: None, raising=False)
    with pytest.raises(urllib.error.HTTPError):
        gate.fetch_artifacts('isolated/repo', 'isolated', NORMAL)
    assert len(calls) == 3


@pytest.mark.parametrize('payload', [b'invalid JSON', b'{"artifacts": null}'])
def test_invalid_marker_response_never_becomes_permission_to_send(monkeypatch, payload):
    calls = []
    def urlopen(request, **kwargs):
        calls.append(request.full_url)
        return io.BytesIO(payload)
    monkeypatch.setattr(gate.urllib.request, 'urlopen', urlopen)
    with pytest.raises((ValueError, RuntimeError)):
        gate.fetch_artifacts('isolated/repo', 'isolated', NORMAL)
    assert len(calls) == 1


@pytest.mark.parametrize('error', [TimeoutError('body timeout'), IncompleteRead(b'partial', 20)])
def test_full_marker_body_is_reopened_after_transient_read_failure(monkeypatch, error):
    calls = []
    class Response(io.BytesIO):
        def read(self, *args):
            if len(calls) == 1:
                raise error
            return super().read(*args)
    def urlopen(request, **kwargs):
        calls.append(request.full_url)
        return Response(b'{"artifacts": []}')
    monkeypatch.setattr(gate.urllib.request, 'urlopen', urlopen)
    monkeypatch.setattr(gate, 'sleep', lambda seconds: None)
    assert gate.fetch_artifacts('isolated/repo', 'isolated', NORMAL) == {'artifacts': []}
    assert len(calls) == 2 and calls[0] == calls[1]


@pytest.mark.parametrize('code', [403, 429])
def test_marker_hard_source_response_remains_single_attempt(monkeypatch, code):
    calls = []
    def urlopen(request, **kwargs):
        calls.append(request.full_url)
        raise urllib.error.HTTPError(request.full_url, code, 'source rejection', {}, None)
    monkeypatch.setattr(gate.urllib.request, 'urlopen', urlopen)
    monkeypatch.setattr(gate, 'sleep', lambda seconds: None)
    with pytest.raises(urllib.error.HTTPError) as exc:
        gate.fetch_artifacts('isolated/repo', 'isolated', NORMAL)
    assert exc.value.code == code and len(calls) == 1


def test_readonly_receipt_archive_retry_does_not_repeat_acceptance_or_metadata(monkeypatch):
    artifact, responses, payload = trusted_receipt()
    api_calls, archive_calls = [], []
    def urlopen(request, **kwargs):
        api_calls.append(request.full_url)
        return responses.pop(0)
    def download(item, token):
        archive_calls.append(item['id'])
        if len(archive_calls) == 1:
            raise urllib.error.HTTPError(item['archive_download_url'], 504, 'Gateway Time-out', {}, None)
        return payload
    monkeypatch.setattr(gate.urllib.request, 'urlopen', urlopen)
    monkeypatch.setattr(receipt_transport, 'download', download)
    monkeypatch.setattr(gate, 'sleep', lambda seconds: None)
    monkeypatch.setattr(gate, 'now_utc', lambda: RECEIPT_NOW)
    gate.verify_smtp_receipt('o/r', 'isolated', 'https://api.github.com', artifact, RECEIPT_DAY, 'close_confirmed')
    assert len(api_calls) == 2 and archive_calls == [7, 7]


def test_bad_receipt_archive_is_not_retried_as_transport_failure(monkeypatch):
    artifact, responses, payload = trusted_receipt(lambda r,j,s,p: p.update(message_id='invalid'))
    calls = []
    monkeypatch.setattr(gate.urllib.request, 'urlopen', lambda *args, **kwargs: responses.pop(0))
    def download(item, token):
        calls.append(item['id'])
        return payload
    monkeypatch.setattr(receipt_transport, 'download', download)
    monkeypatch.setattr(gate, 'sleep', lambda seconds: None)
    monkeypatch.setattr(gate, 'now_utc', lambda: RECEIPT_NOW)
    with pytest.raises(RuntimeError, match='acceptance record'):
        gate.verify_smtp_receipt('o/r', 'isolated', 'https://api.github.com', artifact, RECEIPT_DAY, 'close_confirmed')
    assert calls == [7]
