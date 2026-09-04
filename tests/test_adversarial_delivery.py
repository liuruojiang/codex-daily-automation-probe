"""Hostile transport fixtures; no network, credentials or real mail are used."""
import io
import json
import sys
import subprocess
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from hypothesis import given, settings, strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import mail_utils
import check_ic_im_v1_3_delivery as ic_gate
import check_microcap_delivery as micro_gate
import prepare_ic_im_v1_3_marker as marker
import restore_ic_im_v1_3_ledger as restore
import build_ic_im_v1_3_digest as ic_digest


@pytest.mark.parametrize('ssl', ['true', 'false'])
def test_partial_smtp_rejection_must_fail_delivery(monkeypatch, ssl):
    for key, value in {'MAIL_SERVER':'smtp.invalid', 'MAIL_PORT':'465' if ssl == 'true' else '587',
                       'MAIL_USERNAME':'test', 'MAIL_PASSWORD':'not-a-secret', 'MAIL_FROM':'a@example.invalid',
                       'MAIL_TO':'b@example.invalid,c@example.invalid', 'MAIL_USE_SSL':ssl}.items():
        monkeypatch.setenv(key, value)
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    smtp.send_message.return_value = {'c@example.invalid': (550, b'refused')}
    monkeypatch.setattr(mail_utils.smtplib, 'SMTP_SSL', lambda *a, **k: smtp)
    monkeypatch.setattr(mail_utils.smtplib, 'SMTP', lambda *a, **k: smtp)
    with pytest.raises(mail_utils.smtplib.SMTPRecipientsRefused):
        mail_utils.send_mail('test only', 'simulated body')
    assert smtp.send_message.call_count == 1


def test_microcap_realtime_and_close_have_distinct_delivery_keys():
    day = date(2026, 9, 4)
    assert micro_gate.delivery_marker_name(day, 'realtime') != micro_gate.delivery_marker_name(day, 'close_confirmed')


def test_ic_gate_searches_past_first_hundred_artifacts(monkeypatch):
    calls = []
    name = ic_gate.marker_prefix(date(2026, 9, 4), 'close_confirmed') + 'a'*12
    def get(req, **kwargs):
        calls.append(req.full_url)
        page = [{'name':'unrelated'}] * 100 if len(calls) == 1 else [{'name':name, 'expired':False}]
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({'total_count':101, 'artifacts':page}).encode()
        return response
    monkeypatch.setattr(ic_gate.urllib.request, 'urlopen', get)
    assert ic_gate.marker_exists(ic_gate.fetch_artifacts('owner/repo', 'fake-token', 'https://api.invalid'), name[:-12])
    assert len(calls) == 2


@pytest.mark.parametrize('day,digest', [('2026-99-99','a'*64), ('2026-09-04junk','a'*64), ('2026-09-04','z'*64), ('2026-09-04','\n'*64)])
def test_marker_rejects_invalid_calendar_dates_and_non_sha(day, digest):
    with pytest.raises(ValueError):
        marker.marker_name({'status':'ok', 'strategy_revision':'r7', 'publication_mode':'close_confirmed', 'market_date':day, 'digest':digest})


@settings(max_examples=40)
@given(st.sampled_from(['realtime', 'close_confirmed']), st.dates(min_value=date(2020,1,1), max_value=date(2030,12,31)), st.binary(min_size=32,max_size=32))
def test_marker_and_gate_roundtrip(mode, day, digest):
    name = marker.marker_name({'status':'ok', 'strategy_revision':'r7', 'publication_mode':mode, 'market_date':day.isoformat(), 'digest':digest.hex()})
    assert ic_gate.marker_exists({'artifacts':[{'name':name, 'expired':False}]}, ic_gate.marker_prefix(day, mode))
    assert not ic_gate.marker_exists({'artifacts':[{'name':name, 'expired':True}]}, ic_gate.marker_prefix(day, mode))


def test_ledger_missing_migration_fails_before_destination_write(tmp_path):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr('latest.json', '{}')
    destination = tmp_path/'state'
    with pytest.raises(RuntimeError, match='migration_record'):
        restore.extract(data.getvalue(), destination)
    assert not destination.exists() or not list(destination.rglob('*'))


@pytest.mark.parametrize('member', ['latest.json', 'journal/duplicate.json'])
def test_ledger_duplicate_zip_members_rejected(member):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr('latest.json', '{}')
        archive.writestr('migration_record.json', '{}')
        if member != 'latest.json':
            archive.writestr(member, '{}')
        archive.writestr(member, '{"different":true}')
    with pytest.raises(RuntimeError, match='duplicate'):
        restore.safe_members(data.getvalue())


def test_ledger_restore_skips_failed_and_non_main_runs(monkeypatch):
    def get(req, **kwargs):
        url = req.full_url
        if '/artifacts?' in url:
            payload = {'artifacts':[{'id':i, 'name':restore.ARTIFACT_NAME, 'expired':False,
                'created_at':f'2026-09-0{i}T00:00:00Z', 'archive_download_url':f'https://api.invalid/repos/o/r/actions/artifacts/{i}/zip',
                'workflow_run':{'id':i}} for i in (3,2,1)]}
        else:
            run = int(url.rsplit('/',1)[1])
            payload = {'id':run, 'status':'completed', 'conclusion':'failure' if run == 3 else 'success',
                       'head_branch':'feature' if run == 2 else 'main', 'path':'.github/workflows/ic-im-v1-3-daily-digest.yml'}
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        return response
    monkeypatch.setattr(restore.urllib.request, 'urlopen', get)
    assert restore.fetch_latest('o/r','fake-token','https://api.invalid')['id'] == 1


@pytest.mark.parametrize('mutate', [
    {'publication_mode':'typo'}, {'completed_day':'2026-09-03'}, {'verified_day':'2026-09-03'},
    {'signals':{'IC':{}, 'IM':{}}},
])
def test_success_digest_rejects_inconsistent_close_payload(mutate):
    from test_ic_im_v1_3_daily_digest import signal
    payload = {'status':'ok','strategy_revision':'r7','build':'v1.3-test-r7',
               'publication_mode':'close_confirmed', 'market_date':'2026-09-04','completed_day':'2026-09-04',
               'verified_day':'2026-09-04','next_trade_day':'2026-09-07','digest':'a'*64,
               'signals':{'IC':signal('IC'),'IM':signal('IM')}}
    payload.update(mutate)
    with pytest.raises(ValueError):
        ic_digest.build_success(payload, '', '')


@pytest.mark.parametrize('family', ['micro', 'ic'])
@pytest.mark.parametrize('complete', [False, True])
def test_pending_intent_blocks_ambiguous_smtp_but_completed_marker_wins(monkeypatch, family, complete):
    day = date(2026,9,4)
    gate = micro_gate if family == 'micro' else ic_gate
    name = micro_gate.delivery_marker_name(day, 'close_confirmed') if family == 'micro' else ic_gate.marker_prefix(day, 'close_confirmed')+'a'*12
    artifacts = [{'name':name+'-send-intent', 'expired':False}]
    if complete:
        artifacts.append({'name':name, 'expired':False})
    monkeypatch.setattr(gate, 'fetch_artifacts', lambda *a: {'artifacts':artifacts})
    monkeypatch.setattr(gate, 'now_utc', lambda: datetime(2026,9,4,9,tzinfo=timezone.utc))
    monkeypatch.setattr(sys, 'argv', ['gate','--publication-mode','close_confirmed','--repository','o/r','--token','fake'])
    outputs=[]
    monkeypatch.setattr(gate, 'write_outputs', outputs.append)
    if complete:
        assert gate.main() == 0
        assert outputs[0]['should_send'] == 'false'
    else:
        with pytest.raises(RuntimeError, match='BLOCKED.*uncertain'):
            gate.main()
        assert not outputs


@pytest.mark.parametrize('mode', ['realtime', 'close_confirmed', 'unknown'])
def test_legacy_marker_requires_exact_metadata_mode_and_date(monkeypatch, mode):
    report = {'name':'microcap-realtime-digest', 'expired':False,'id':42,
              'archive_download_url':'https://api.invalid/repos/o/r/actions/artifacts/42/zip'}
    response = MagicMock()
    response.__enter__.return_value.read.return_value = json.dumps({'artifacts':[report]}).encode()
    monkeypatch.setattr(micro_gate.urllib.request, 'urlopen', lambda *a,**k: response)
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as archive:
        archive.writestr('artifacts/metadata.json', json.dumps({'status':'OK','signal_date':'2026-09-04','publication_mode':mode}))
    monkeypatch.setattr(restore, 'download', lambda *a: data.getvalue())
    args=('o/r','fake','https://api.invalid',{'workflow_run':{'id':1}},date(2026,9,4))
    if mode == 'unknown':
        with pytest.raises(RuntimeError, match='BLOCKED'):
            micro_gate.legacy_marker_mode(*args)
    else:
        assert micro_gate.legacy_marker_mode(*args) == mode
    with pytest.raises(RuntimeError, match='BLOCKED'):
        micro_gate.legacy_marker_mode(*args[:-1],date(2026,9,5))


def test_smtp_timeout_is_not_automatically_retried(monkeypatch):
    for key, value in {'MAIL_SERVER':'smtp.invalid','MAIL_PORT':'465','MAIL_USERNAME':'test',
                       'MAIL_PASSWORD':'fake','MAIL_FROM':'a@example.invalid','MAIL_TO':'b@example.invalid'}.items():
        monkeypatch.setenv(key,value)
    smtp=MagicMock()
    smtp.__enter__.return_value=smtp
    smtp.send_message.side_effect=TimeoutError('ambiguous DATA acknowledgement')
    monkeypatch.setattr(mail_utils.smtplib,'SMTP_SSL',lambda *a,**k:smtp)
    with pytest.raises(TimeoutError):
        mail_utils.send_mail('fixture','fixture')
    assert smtp.send_message.call_count == 1


@pytest.mark.parametrize('scenario', ['one_fails','transient','timeout','body_disconnect','body_timeout','body_403'])
def test_real_worker_dispatches_independently_and_bounds_retries(scenario):
    root=Path(__file__).resolve().parents[1]
    source=(root/'cloudflare-workers/microcap-post-close-trigger/worker.js').read_text(encoding='utf-8')
    source=source.replace('export default {', 'const worker = {')
    harness=r'''
const calls = {};
globalThis.setTimeout = (callback) => { callback(); return 1; };
globalThis.fetch = async (url, options) => {
  const name = url.split('/').at(-2);
  calls[name] = (calls[name] || 0) + 1;
  if (!options.signal) throw new Error('MISSING_TIMEOUT_SIGNAL');
  const scenario = SCENARIO;
  if (name.startsWith('microcap') && scenario === 'one_fails')
    return {ok:false,status:403,text:async ()=>'rejected fixture'};
  if (name.startsWith('microcap') && scenario === 'timeout')
    throw new DOMException('fixture timed out', 'TimeoutError');
  if (name.startsWith('microcap') && scenario.startsWith('body_') &&
      (scenario !== 'body_disconnect' || calls[name] < 3))
    return {ok:false,status:scenario === 'body_403' ? 403 : 503,text:async () => {
      if (scenario === 'body_disconnect') throw new TypeError('fixture body disconnected');
      throw new DOMException('fixture body timed out', 'TimeoutError');
    }};
  if (scenario === 'transient' && calls[name] < 3)
    return {ok:false,status:503,text:async ()=>'fixture unavailable'};
  return {ok:true,status:204};
};
let error = '';
try { await dispatchAllDigests({GITHUB_TOKEN:'fixture-only'}); }
catch(e) { error = String(e); }
console.log(JSON.stringify({calls,error}));
'''.replace('SCENARIO', json.dumps(scenario))
    result=subprocess.run(['node','--input-type=module'],input=source+'\n'+harness,text=True,capture_output=True,timeout=10,check=True)
    observed=json.loads(result.stdout.strip().splitlines()[-1])
    assert 'MISSING_TIMEOUT_SIGNAL' not in observed['error']
    assert len(observed['calls']) == 2
    assert all(1 <= count <= 3 for count in observed['calls'].values())
    if scenario in ('transient','body_disconnect'):
        assert observed['error'] == ''
        assert observed['calls']['microcap-realtime-digest.yml'] == 3
        assert observed['calls']['ic-im-v1-3-daily-digest.yml'] == (3 if scenario == 'transient' else 1)
    else:
        assert observed['error']
        assert observed['calls']['ic-im-v1-3-daily-digest.yml'] == 1
        assert observed['calls']['microcap-realtime-digest.yml'] == (1 if scenario in ('one_fails','body_403') else 3)
