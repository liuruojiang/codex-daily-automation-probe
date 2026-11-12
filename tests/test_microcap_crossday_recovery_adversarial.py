"""Independent fault cases and a dedicated, no-send acceptance artifact reader.

The artifact namespace below is deliberately different from formal member
checkpoints. Software dates in these tests never become production signals.
"""
from __future__ import annotations

import argparse
import copy
import csv
from datetime import date, datetime
import hashlib
import importlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import shutil
import stat
import subprocess
import sys
import urllib.request
import uuid
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
REPOSITORY = 'liuruojiang/codex-daily-automation-probe'
WORKFLOW = '.github/workflows/microcap-crossday-recovery-acceptance.yml'
ARTIFACT_PREFIX = 'microcap-crossday-acceptance-v1-'
ARTIFACT_FILE = 'recovery-acceptance-state.zip'
MANIFEST = 'acceptance-manifest.json'
SCHEMA = 'microcap_crossday_software_acceptance_v1'
MAX_BYTES = 32 * 1024 * 1024
PRODUCE_STEP = 'Produce isolated recovery acceptance state'


def sha(body):
    return hashlib.sha256(body).hexdigest()


def exact_sha(value):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{40}', value) is None:
        raise ValueError('Acceptance requires an exact automation commit')
    return value


def artifact_name(head):
    return ARTIFACT_PREFIX + exact_sha(head)


def confined_name(value):
    if not isinstance(value, str):
        raise ValueError('Non-string acceptance member')
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or '\\' in value or '..' in path.parts
            or any(':' in part for part in path.parts) or path.as_posix() != value):
        raise ValueError('Unsafe acceptance member')
    return path


def pack_state(directory, head):
    """Package only an explicitly isolated software acceptance state."""
    directory = Path(directory)
    files = {}
    payload = {}
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise ValueError('Acceptance state cannot contain links')
        if not path.is_file():
            continue
        name = path.relative_to(directory).as_posix()
        confined_name(name)
        if name == MANIFEST:
            raise ValueError('Do not repack a restored acceptance manifest')
        raw = path.read_bytes()
        files[name] = sha(raw)
        payload[name] = raw
    if not files or sum(map(len, payload.values())) > MAX_BYTES:
        raise ValueError('Acceptance state is empty or oversized')
    record = dict(schema=SCHEMA, automation_head=exact_sha(head),
                  software_observations=True, current_signal_ready=False,
                  SMTP_calls=0, orders_placed=False, files=files)
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(MANIFEST, json.dumps(record, sort_keys=True).encode())
        for name, raw in payload.items():
            archive.writestr(name, raw)
    return result.getvalue()


def unpack_state(raw, out, head):
    """Validate the complete inner closure before writing any member."""
    if len(raw) > MAX_BYTES:
        raise ValueError('Acceptance inner archive is oversized')
    payload = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)) or len(names) > 512:
            raise ValueError('Acceptance archive has duplicate or excessive members')
        if sum(info.file_size for info in infos) > MAX_BYTES:
            raise ValueError('Acceptance extraction is oversized')
        for info in infos:
            confined_name(info.filename)
            if (info.is_dir() or info.flag_bits & 1
                    or stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK):
                raise ValueError('Acceptance archive member is not a regular file')
            payload[info.filename] = archive.read(info)
    if MANIFEST not in payload:
        raise ValueError('Acceptance manifest is missing')
    manifest = json.loads(payload[MANIFEST])
    if (manifest.get('schema') != SCHEMA or manifest.get('automation_head') != exact_sha(head)
            or manifest.get('software_observations') is not True
            or manifest.get('current_signal_ready') is not False
            or manifest.get('SMTP_calls') != 0 or manifest.get('orders_placed') is not False):
        raise ValueError('Acceptance manifest is not an exact no-send software state')
    expected = manifest.get('files')
    if not isinstance(expected, dict) or not expected or set(expected) != set(payload) - {MANIFEST}:
        raise ValueError('Acceptance manifest does not cover its exact files')
    for name, digest in expected.items():
        confined_name(name)
        if sha(payload[name]) != digest:
            raise ValueError('Acceptance payload changed: ' + name)
    out = Path(out)
    if out.exists():
        raise FileExistsError('Acceptance restore must use a new directory')
    out.mkdir(parents=True)
    for name, body in payload.items():
        path = out.joinpath(*PurePosixPath(name).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    return manifest


def api_json(path, token):
    from restore_microcap_verified_state import api_request
    request = api_request('https://api.github.com/repos/' + REPOSITORY + '/actions/' + path, token)
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('Acceptance metadata response is oversized')
    return json.loads(raw)


def download_artifact(artifact, token):
    from restore_microcap_verified_state import api_request, StripCrossOriginAuthRedirectHandler
    opener = urllib.request.build_opener(StripCrossOriginAuthRedirectHandler())
    with opener.open(api_request(artifact['archive_download_url'], token), timeout=60) as response:
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('Acceptance artifact is oversized')
    return raw


def restore_acceptance(source_run, head, out, *, token=None, read_json=None, download=None):
    """Exactly one known successful producer; no latest search or seed fallback."""
    if type(source_run) is not int or source_run <= 0:
        raise ValueError('Exact source acceptance run is required')
    head = exact_sha(head)
    token = os.environ.get('GITHUB_TOKEN', '') if token is None else token
    get = (lambda path: api_json(path, token)) if read_json is None else read_json
    fetch = (lambda item: download_artifact(item, token)) if download is None else download
    run = get('runs/' + str(source_run))
    if (run.get('id') != source_run or run.get('head_sha') != head
            or run.get('head_branch') != 'main' or run.get('status') != 'completed'
            or run.get('conclusion') != 'success' or run.get('event') != 'workflow_dispatch'
            or run.get('path', '').split('@', 1)[0] != WORKFLOW):
        raise ValueError('Acceptance source run identity/status differs')
    jobs = get('runs/' + str(source_run) + '/jobs?per_page=100').get('jobs', [])
    if not any(any(step.get('name') == PRODUCE_STEP and step.get('conclusion') == 'success'
                   for step in job.get('steps', [])) for job in jobs):
        raise ValueError('Source run did not produce the isolated acceptance state')
    listing = get('runs/' + str(source_run) + '/artifacts?per_page=100')
    if listing.get('total_count', 0) > 100:
        raise ValueError('Unexpected acceptance artifact pagination')
    items = [item for item in listing.get('artifacts', []) if item.get('name') == artifact_name(head)]
    if len(items) != 1:
        raise ValueError('Exact acceptance artifact is absent or ambiguous; fallback is disabled')
    artifact = items[0]
    artifact_id = artifact.get('id')
    expected_url = f'https://api.github.com/repos/{REPOSITORY}/actions/artifacts/{artifact_id}/zip'
    owner = artifact.get('workflow_run', {})
    digest = artifact.get('digest', '')
    if (type(artifact_id) is not int or artifact_id <= 0 or artifact.get('expired') is not False
            or owner.get('id') != source_run or owner.get('head_sha') != head
            or owner.get('head_branch') != 'main' or artifact.get('archive_download_url') != expected_url
            or re.fullmatch('sha256:[0-9a-f]{64}', digest) is None
            or type(artifact.get('size_in_bytes')) is not int
            or not 0 < artifact['size_in_bytes'] <= MAX_BYTES):
        raise ValueError('Acceptance artifact metadata differs')
    raw = fetch(artifact)
    if len(raw) != artifact['size_in_bytes'] or sha(raw) != digest[7:]:
        raise ValueError('Acceptance artifact GitHub digest differs')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        if (len(infos) != 1 or infos[0].filename != ARTIFACT_FILE
                or infos[0].is_dir() or infos[0].file_size > MAX_BYTES
                or infos[0].flag_bits & 1
                or stat.S_IFMT(infos[0].external_attr >> 16) == stat.S_IFLNK):
            raise ValueError('Unexpected acceptance artifact outer member')
        inner = archive.read(infos[0])
    manifest = unpack_state(inner, out, head)
    return dict(schema='microcap_crossday_acceptance_readback_v1', source_run=source_run,
                artifact_id=artifact_id, artifact_name=artifact['name'],
                github_artifact_sha256=sha(raw), inner_sha256=sha(inner),
                automation_head=head, restored=True, fallback_used=False,
                current_signal_ready=False, SMTP_calls=0, orders_placed=False,
                restored_files=manifest['files'])


@pytest.fixture(autouse=True)
def forbid_network_and_smtp(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError('Independent acceptance cannot contact markets, SMTP or dispatch')
    monkeypatch.setattr(socket.socket, 'connect', refuse)
    monkeypatch.setattr(socket, 'create_connection', refuse)


@pytest.fixture
def acceptance_transport(tmp_path):
    head = 'a' * 40
    state = tmp_path / 'isolated-source'
    state.mkdir()
    (state / 'journal.json').write_text(json.dumps(dict(phase='sources_complete', software_only=True)))
    inner = pack_state(state, head)
    content = io.BytesIO()
    with zipfile.ZipFile(content, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(ARTIFACT_FILE, inner)
    outer = content.getvalue()
    run = dict(id=91, head_sha=head, head_branch='main', status='completed', conclusion='success',
               event='workflow_dispatch', path=WORKFLOW)
    artifact = dict(id=92, name=artifact_name(head), expired=False,
                    archive_download_url=f'https://api.github.com/repos/{REPOSITORY}/actions/artifacts/92/zip',
                    workflow_run=dict(id=91, head_sha=head, head_branch='main'),
                    digest='sha256:' + sha(outer), size_in_bytes=len(outer))
    data = {'runs/91': run, 'runs/91/jobs?per_page=100': dict(jobs=[dict(steps=[dict(name=PRODUCE_STEP, conclusion='success')])]),
            'runs/91/artifacts?per_page=100': dict(total_count=1, artifacts=[artifact])}
    calls = []
    def get(path):
        calls.append(('GET', path))
        return data[path]
    def download(item):
        calls.append(('DOWNLOAD', item['id']))
        return outer
    return dict(head=head, state=state, inner=inner, outer=outer, data=data, artifact=artifact,
                get=get, download=download, calls=calls, out=tmp_path / 'new-reader')


def test_exact_successful_previous_acceptance_artifact_is_restored_without_fallback(acceptance_transport):
    f = acceptance_transport
    result = restore_acceptance(91, f['head'], f['out'], read_json=f['get'], download=f['download'])
    assert result['source_run'] == 91 and result['artifact_id'] == 92
    assert result['restored'] is True and result['fallback_used'] is False
    assert (f['out'] / 'journal.json').read_bytes() == (f['state'] / 'journal.json').read_bytes()
    assert len([call for call in f['calls'] if call[0] == 'DOWNLOAD']) == 1


@pytest.mark.parametrize('field,value', [('head_sha','b'*40), ('head_branch','other'),
    ('status','in_progress'), ('conclusion','failure'), ('path','.github/workflows/microcap-member-daily.yml'),
    ('event','push'), ('id',90)])
def test_wrong_or_unfinished_source_run_never_downloads(acceptance_transport, field, value):
    f = acceptance_transport
    f['data']['runs/91'][field] = value
    with pytest.raises(ValueError, match='source run'):
        restore_acceptance(91, f['head'], f['out'], read_json=f['get'], download=f['download'])
    assert not f['out'].exists() and not any(call[0] == 'DOWNLOAD' for call in f['calls'])


@pytest.mark.parametrize('fault', ['missing','duplicate','expired','wrong_owner','wrong_digest','unsafe_url','producer_not_run'])
def test_absent_ambiguous_or_unbound_acceptance_has_no_fallback(acceptance_transport, fault):
    f = acceptance_transport
    listing = f['data']['runs/91/artifacts?per_page=100']
    if fault == 'missing': listing['artifacts'] = []
    elif fault == 'duplicate': listing['artifacts'].append(copy.deepcopy(f['artifact']))
    elif fault == 'expired': f['artifact']['expired'] = True
    elif fault == 'wrong_owner': f['artifact']['workflow_run']['id'] = 90
    elif fault == 'wrong_digest': f['artifact']['digest'] = 'sha256:' + 'f' * 64
    elif fault == 'unsafe_url': f['artifact']['archive_download_url'] = 'https://untrusted.invalid/archive.zip'
    else: f['data']['runs/91/jobs?per_page=100']['jobs'][0]['steps'][0]['conclusion'] = 'skipped'
    with pytest.raises(ValueError):
        restore_acceptance(91, f['head'], f['out'], read_json=f['get'], download=f['download'])
    assert not f['out'].exists()


@pytest.mark.parametrize('bad', ['../escape.json','/absolute.json','C:/outside.json','..\\outside.json'])
def test_acceptance_manifest_cannot_extract_outside_a_new_reader(tmp_path, bad):
    value = io.BytesIO()
    body = b'no escape'
    with zipfile.ZipFile(value, 'w') as archive:
        archive.writestr(bad, body)
        archive.writestr(MANIFEST, json.dumps(dict(schema=SCHEMA, automation_head='a'*40,
            software_observations=True, current_signal_ready=False, SMTP_calls=0, orders_placed=False,
            files={bad:sha(body)})))
    out = tmp_path / 'restore'
    with pytest.raises(ValueError, match='Unsafe'):
        unpack_state(value.getvalue(), out, 'a'*40)
    assert not out.exists()


def test_acceptance_artifact_cannot_be_mislabelled_as_formal_477_checkpoint():
    name = artifact_name('a'*40)
    assert name.startswith(ARTIFACT_PREFIX)
    assert not name.startswith('microcap-member-checkpoint-')
    with pytest.raises(ValueError):
        artifact_name('main')


def test_dedicated_cloud_acceptance_has_no_production_dispatch_or_smtp_path():
    import yaml
    flow = yaml.load((ROOT / WORKFLOW).read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
    assert flow['permissions'] == {'contents': 'read', 'actions': 'read'}
    assert flow['concurrency']['group'] != 'microcap-independent-maintenance'
    steps = flow['jobs']['acceptance']['steps']
    text = json.dumps(flow)
    for prohibited in ('send_report.py', 'check_microcap_delivery.py', 'MAIL_PASSWORD',
                       'workflow_dispatches', 'gh workflow run', 'repository_dispatch'):
        assert prohibited not in text
    uploads = [step['with']['name'] for step in steps if step.get('uses', '').startswith('actions/upload-artifact@')]
    assert uploads and all(name.startswith('microcap-crossday-acceptance-') for name in uploads)
    assert not any('microcap-member-checkpoint' in name for name in uploads)
    producer = next(step for step in steps if step.get('name') == PRODUCE_STEP)
    consumer = next(step for step in steps if step.get('name') == 'Restore exact previous acceptance artifact')
    assert producer['if'] == "inputs.phase == 'produce'"
    assert consumer['if'] == "inputs.phase == 'readback'"
    assert 'source-run' in consumer['run']


def csv_bytes(rows, fields=None):
    text = io.StringIO(newline='')
    writer = csv.DictWriter(text, fieldnames=fields or list(rows[0]), lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return text.getvalue().encode('utf-8')


def raw_package(checkpoint, directory):
    document = json.loads(Path(checkpoint).read_bytes())
    return document, {role: (Path(directory) / document[role]['path']).read_bytes()
                      for role in ('period', 'history', 'turnover', 'version_state', 'prices')}


def as_zip(document, payload):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('checkpoint.json', json.dumps(document, ensure_ascii=False).encode())
        for role, raw in payload.items():
            archive.writestr(document[role]['path'], raw)
    return output.getvalue()


class SoftwareRecoveryClock(datetime):
    @classmethod
    def now(cls, tz=None):
        value=datetime.fromisoformat('2026-11-13T17:00:00+08:00')
        return value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)


@pytest.fixture(scope='module')
def independently_built_recovery(tmp_path_factory):
    """Real M7 math and packaging on a named software suffix, no market data.

    The source Git read is real. Only the data-root -> source-module binding
    and the independent calendar transport are redirected into this fixture.
    """
    from unittest.mock import patch
    import pandas as pd
    api = importlib.import_module('microcap_recovery_handoff')
    native_root = Path(os.environ.get('MICROCAP_NATIVE_ROOT')
        or os.environ.get('MICROCAP_RECOVERY_NATIVE_ROOT')
        or r'D:\Codex\workspaces\microcap-source-reader-m7-20261112').resolve()
    if not native_root.is_dir():
        pytest.fail('Exact native M7 source checkout is required')
    real_runtime = api._runtime
    actual_identity = real_runtime(native_root)
    native = api._native(native_root)
    h, daily, calendar_module, _, _ = native
    source_hashes = {}
    for path in ('scripts/member_period_handoff.py', 'scripts/member_daily_signal.py',
                 'microcap_top100_mom16_biweekly_live_v2_0.py',
                 'microcap_top100_mom16_biweekly_live_v2_3.py',
                 'microcap_top100_mom16_biweekly_live_v2_5.py'):
        original = subprocess.check_output(['git', '-C', str(native_root), 'show', actual_identity + ':' + path])
        assert original.replace(b'\r\n', b'\n') == (native_root/path).read_bytes().replace(b'\r\n', b'\n')
        source_hashes[path] = sha(original)
    assert Path(h.__file__).resolve().is_relative_to(native_root)
    sessions_doc = json.loads((native_root/'tests/fixtures/exchange_sessions_20261015.json').read_bytes())
    sessions = sessions_doc['sessions']
    temp = tmp_path_factory.mktemp('independent_crossday_native')
    parent_dir = temp/'parent'
    parent_dir.mkdir()
    seed = ROOT/'tests/fixtures/member_checkpoint_local_read_20261111/checkpoint-13838054871.zip'
    with zipfile.ZipFile(seed) as archive:
        for name in archive.namelist():
            confined_name(name)
            (parent_dir/name).write_bytes(archive.read(name))
    parent_path = parent_dir/'checkpoint.json'
    parent = h.verify(parent_path)
    old = {role:h.read_bound(parent,role) for role in api.ROLES}
    old_period = json.loads(old['period'])
    old_history = list(csv.DictReader(io.StringIO(old['history'].decode('utf-8-sig'))))
    last = old_history[-1]
    suffix = [dict(last,date=day) for day in sessions if '2026-11-11'<day<='2026-11-13']
    assert [row['date'] for row in suffix] == ['2026-11-12','2026-11-13']
    history = api._append(old['history'],suffix)
    history_rows = list(csv.DictReader(io.StringIO(history.decode('utf-8-sig'))))
    data = temp/'formal-software'
    (data/'outputs').mkdir(parents=True)
    executed = [dict(rank=row['rank'],symbol=row['symbol'],as_of_date='2026-11-13')
                for row in old_period['effective_members']]
    targets = [dict(rank=row['rank'],symbol=row['symbol'],name=row.get('name',''),rebalance_date='2026-11-12')
               for row in old_period['effective_members']]
    events = list(csv.DictReader(io.StringIO(old['turnover'].decode('utf-8-sig'))))
    fields = list(events[0])
    new_event = {key:'0' for key in fields}
    new_event.update(rebalance_date='2026-11-12',execution_timing='close',constraint_trade_date='2026-11-12',
        execution_date='2026-11-12',effective_date='2026-11-12',return_start_date='2026-11-13',holding_count_after='100')
    turnover = csv_bytes(events+[new_event],fields)
    exports = {api.EXPORTS['proxy']:csv_bytes([dict(date=row['date'],close=row['microcap']) for row in history_rows]),
        api.EXPORTS['panel']:csv_bytes([{'date':row['date'],'1.000852':row['hedge']} for row in history_rows]),
        api.EXPORTS['effective']:csv_bytes(executed),api.EXPORTS['members']:csv_bytes(targets),api.EXPORTS['turnover']:turnover}
    frame = pd.read_csv(io.BytesIO(history)).set_index('date')
    frame.index = pd.to_datetime(frame.index)
    event_frame = pd.read_csv(io.BytesIO(turnover))
    for version,module in daily.native_modules().items():
        row,_ = daily.calculate_native(version,module,frame,event_frame)
        row['official_close_confirmed_signal'] = True
        row['signal_timing'] = 'close_confirmed'
        exports[api.EXPORTS[version]] = row.to_csv(index=False).encode('utf-8')
    inputs = {}
    for name,raw in exports.items():
        (data/name).write_bytes(raw)
        inputs[name] = sha(raw.replace(b'\r\n',b'\n'))
    for row in json.loads(old['prices'])['rows']:
        name='.microcap_index_cache/prices_raw/'+row['symbol']+'.csv'
        path=data/name
        path.parent.mkdir(parents=True,exist_ok=True)
        raw=csv_bytes([dict(date='2026-11-13',close_raw=row['previous_raw_close'])])
        path.write_bytes(raw)
        inputs[name]=sha(raw.replace(b'\r\n',b'\n'))
    manifest=dict(ok=True,status='complete',scope='whole_workspace_delivery',expected_date='2026-11-13',
        release_sha=api.NATIVE,inputs=inputs,artifacts={},explicit_software_formal_envelope=True,
        verified_at='2026-11-13T16:58:00+08:00',
        observed_market_data=False,not_a_production_refresh_all_or_check=True)
    (data/'outputs/top100_delivery_manifest.json').write_bytes(api.encode(manifest))
    patches = [patch.object(api,'_native',lambda ignored:native),
        patch.object(api,'_runtime',lambda ignored:real_runtime(native_root)),
        patch.object(calendar_module,'sessions_for_day',lambda _:tuple(date.fromisoformat(day) for day in sessions)),
        patch.object(calendar_module,'latest_completed_session',lambda *args,**kwargs:date(2026,11,13)),
        patch.object(api,'datetime',SoftwareRecoveryClock)]
    for patcher in patches:
        patcher.start()
    try:
        candidate=temp/'candidate'
        api.bootstrap_formal_candidate(data,'2026-11-13',candidate)
        recovered=temp/'recovered'
        api.build(data,parent_path,candidate/'checkpoint.json','2026-11-13',recovered)
        document,payload=raw_package(recovered/'checkpoint.json',recovered)
        yield dict(api=api,native=native,native_root=native_root,temp=temp,data=data,
            parent=parent_path,parent_original=parent_path.read_bytes(),old_payload=old,
            candidate=candidate/'checkpoint.json',recovered=recovered,checkpoint=document,payload=payload,
            sessions=sessions,native_identity=actual_identity,native_source_hashes=source_hashes,
            seed_sha256=sha(seed.read_bytes()))
    finally:
        for patcher in reversed(patches):
            patcher.stop()


def test_independent_crossday_recovery_executes_real_native_math_and_keeps_original_history(independently_built_recovery):
    case=independently_built_recovery
    api=case['api']
    verified=api.validate_recovery_payload(case['checkpoint'],case['payload'])
    assert verified['sessions']==['2026-11-12','2026-11-13']
    assert verified['rebalance_sessions']==['2026-11-12']
    assert case['payload']['history'].startswith(case['old_payload']['history'])
    assert case['parent'].read_bytes()==case['parent_original']
    assert case['checkpoint']['as_of_date']=='2026-11-13'
    assert case['checkpoint']['next_trade_date']=='2026-11-16'
    assert json.loads(case['payload']['period'])['anchor_day']=='2026-11-12'


def _actual_recovery_plan(case, completed='2026-11-13', *, task='rebalance_recovery'):
    import pandas as pd
    planner=importlib.import_module('microcap_recovery_plan')
    h,_,_,base,_=case['native']
    parent=h.verify(case['parent'])
    period=json.loads(h.read_bound(parent,'period'))
    observation=dict(consumer_sha=case['api'].NATIVE,latest_completed_session=completed,
        sessions=case['sessions'],rebalance_dates=list(base.build_biweekly_rebalance_dates(
            pd.DatetimeIndex(case['sessions'])).strftime('%Y-%m-%d')),
        checkpoint_path=str(case['parent']),checkpoint_sha256=sha(case['parent'].read_bytes()),
        checkpoint_as_of_date=parent['as_of_date'],checkpoint_period_status=parent.get('period_status','current'),
        checkpoint_period_anchor=period['anchor_day'],checkpoint_period_sha256=parent['period']['sha256'],
        checkpoint_members=len(period['effective_members']),native_checkpoint_verified=True)
    return planner.build_plan(observation,completed,case['api'].NATIVE,task=task)


def test_independent_plan_keeps_both_missed_native_periods_in_order(independently_built_recovery):
    plan=_actual_recovery_plan(independently_built_recovery,'2026-11-27')
    assert plan['required_rebalance_dates']==['2026-11-12','2026-11-26']
    assert plan['recovery_R']=='2026-11-12' and plan['last_required_R']=='2026-11-26'
    assert plan['parent_not_after_date']=='2026-11-11'


def test_independent_plan_uses_exchange_sessions_not_weekend_or_holiday_dates(independently_built_recovery):
    case=independently_built_recovery
    assert '2026-10-01' not in case['sessions'] and '2026-11-14' not in case['sessions']
    planner=importlib.import_module('microcap_recovery_plan')
    with pytest.raises(ValueError,match='calendar differs'):
        _actual_recovery_plan(case,'2026-11-14')
    # Monday morning's completed exchange close is Friday; the plan keeps the
    # real original Thursday R, with no invented weekend return rows.
    friday=_actual_recovery_plan(case,'2026-11-13')
    assert friday['required_rebalance_dates']==['2026-11-12']
    assert friday['recovery_next_session']=='2026-11-13'


def test_independent_recovery_plan_is_idempotent_for_repeated_09_and_11_observation(independently_built_recovery):
    first=_actual_recovery_plan(independently_built_recovery)
    second=_actual_recovery_plan(independently_built_recovery)
    assert first==second and first['run'] is True
    assert first['source_collection_complete_does_not_skip_recovery'] is True


def _exec_actual_preflight(case, path, plan, monkeypatch, *, target='2026-11-13'):
    driver=importlib.import_module('run_microcap_scoped_rebalance')
    native_delivery=importlib.import_module('scripts.top100_delivery')
    authority=importlib.import_module('scripts.st_history_scope')
    out=Path(path)
    result=out/'preflight-result.json'
    out.mkdir()
    argv=['actual-native-preflight',str(case['data']),target,case['api'].NATIVE,str(result)]
    if plan is not None:
        planning=out/'recovery-plan.json'
        planning.write_bytes(case['api'].encode(plan))
        argv.append(str(planning))
    git_dir=subprocess.check_output(['git','-C',str(case['native_root']),'rev-parse','--absolute-git-dir'],
        text=True,encoding='utf-8').strip()
    with monkeypatch.context() as isolated:
        # Preserve the original native Git identity while the data itself is a
        # managed, clearly labelled software formal-export envelope.
        isolated.setenv('GIT_DIR',git_dir)
        isolated.setenv('GIT_WORK_TREE',str(case['native_root']))
        isolated.setattr(sys,'argv',argv)
        isolated.setattr(native_delivery,'verify_release',lambda root:case['api'].NATIVE)
        isolated.setattr(authority,'resolve_archive_reference',lambda root:('software-authority', 'f'*64))
        exec(compile(driver.PREFLIGHT,'<unchanged-production-scoped-preflight>','exec'),{})
    return json.loads(result.read_bytes())


def test_real_driver_preflight_accepts_latest_non_R_with_exact_missed_R_plan(independently_built_recovery,tmp_path,monkeypatch):
    case=independently_built_recovery
    plan=_actual_recovery_plan(case)
    assert plan['target']=='2026-11-13' and plan['required_rebalance_dates']==['2026-11-12']
    assert plan['parent_not_after_date']=='2026-11-11'
    result=_exec_actual_preflight(case,tmp_path/'with-plan',plan,monkeypatch)
    assert result['target']=='2026-11-13'
    assert result['recovery_rebalance_dates']==['2026-11-12']
    assert result['next_session']=='2026-11-16'


@pytest.mark.parametrize('target,reason',[('2026-11-12','latest completed'),('2026-11-13','rebalance session')])
def test_ordinary_preflight_keeps_original_stale_and_non_R_guards(independently_built_recovery,tmp_path,monkeypatch,target,reason):
    with pytest.raises(ValueError,match=reason):
        _exec_actual_preflight(independently_built_recovery,tmp_path/target,None,monkeypatch,target=target)


@pytest.mark.parametrize('fault',['target','delivery_date','checkpoint_sha','missing_R','phase','parent_ceiling','run'])
def test_real_preflight_rejects_wrong_date_parent_or_missing_period(independently_built_recovery,tmp_path,monkeypatch,fault):
    case=independently_built_recovery
    plan=_actual_recovery_plan(case)
    if fault=='target': plan['target']='2026-11-12'
    elif fault=='delivery_date': plan['delivery_date']='2026-11-12'
    elif fault=='checkpoint_sha': plan['latest_checkpoint_sha256']='0'*64
    elif fault=='missing_R': plan['required_rebalance_dates']=[]
    elif fault=='phase': plan['latest_checkpoint_period_status']='awaiting_handoff'
    elif fault=='parent_ceiling': plan['parent_not_after_date']='2026-11-10'
    else: plan['run']=False
    with pytest.raises(ValueError):
        _exec_actual_preflight(case,tmp_path/fault,plan,monkeypatch)


@pytest.mark.parametrize('fault',['original_row','wrong_mode','wrong_controller','missing_R','missing_session',
    'duplicate_calendar','fake_next_day','changed_member','unbound_original','changed_math_state'])
def test_independent_recovery_proof_faults_cannot_be_resealed_with_payload_hashes(independently_built_recovery,fault):
    case=independently_built_recovery
    api=case['api']
    cp=copy.deepcopy(case['checkpoint'])
    payload=dict(case['payload'])
    period=json.loads(payload['period'])
    proof=period[api.KEY]
    if fault=='original_row':
        history=payload['history'].decode('utf-8-sig')
        rows=list(csv.DictReader(io.StringIO(history)))
        rows[0]['microcap']=str(float(rows[0]['microcap'])*1.01)
        payload['history']=csv_bytes(rows)
    elif fault=='wrong_mode': cp['handoff_mode']='native_close_extends_previous'
    elif fault=='wrong_controller': cp['recovery_controller']['source_sha256']='0'*64
    elif fault=='missing_R': proof['rebalance_sessions']=[]
    elif fault=='missing_session': proof['sessions']=proof['sessions'][1:]
    elif fault=='duplicate_calendar': proof['calendar_sessions'].append(proof['calendar_sessions'][-1])
    elif fault=='fake_next_day': cp['next_trade_date']='2099-01-01'
    elif fault=='changed_member': period['effective_members'][0]['symbol']='699998'
    elif fault=='unbound_original': proof['parent']['history']['sha256']='0'*64
    else:
        states=json.loads(api._unblob(proof['derived_state']))
        states['versions']['v2.3']['microcap_momentum']=99.0
        payload['version_state']=api.encode(states)
        proof['derived_state']=api._blob(payload['version_state'])
    payload['period']=api.encode(period)
    for role,raw in payload.items(): cp[role]['sha256']=sha(raw)
    with pytest.raises((ValueError,KeyError)):
        api.validate_recovery_payload(cp,payload)


def _software_driver_paths(base):
    base=Path(base)
    return dict(consumer=base/'scoped-consumer',out=base/'scoped-delivery',parent=base/'scoped-previous/checkpoint.json',
                config=base/'scoped-prepare/consumer-config.json',plan=base/'scoped-recovery-plan.json')


def _stage_call_counts(out):
    records=[]
    for path in sorted((Path(out)/'commands').glob('*/process.json')):
        records.append(json.loads(path.read_bytes()))
    counts={}
    for record in records:
        label=record['software_stage_label']
        counts[label]=counts.get(label,0)+1
    return counts,records


def _invoke_software_driver(case, paths, *, fail=None, forbid_execution=False):
    """Exercise the real coordinator with native recovery and an explicit gate fixture."""
    driver=importlib.import_module('run_microcap_scoped_rebalance')
    api=case['api']
    consumer,out,parent,config,plan=(paths[key] for key in ('consumer','out','parent','config','plan'))
    out.mkdir(parents=True,exist_ok=True)
    def option(argv,name):
        argv=list(map(str,argv))
        return Path(argv[argv.index(name)+1])
    def run(label,argv):
        if forbid_execution:
            raise AssertionError('Completed producer stage was re-executed after artifact recovery: '+label)
        attempt=out/'commands'/(label+'-'+uuid.uuid4().hex)
        attempt.mkdir(parents=True)
        result={key:attempt/name for key,name in [('stdout','stdout.log'),('stderr','stderr.log'),('process','process.json')]}
        process=dict(command=list(map(str,argv)),software_stage_label=label,explicit_software_command_runner=True,
                     real_market_gate_executed=False,exit_code=0,completion_unconfirmed=False)
        try:
            if label==fail:
                raise RuntimeError('Independent injected failure at '+label)
            if label in ('refresh','check'):
                # Model the external full-market gates; their native algebraic
                # export data was generated by the exact actual M7 functions.
                assert (consumer/'outputs/top100_delivery_manifest.json').is_file()
            elif label=='pack-formal':
                original_manifest=(consumer/'outputs/top100_delivery_manifest.json').read_bytes()
                packed_manifest=json.loads(original_manifest)
                # Match the important behavior of actual M7
                # _pack_shared_locked: ZIP manifest gets a fresh verified_at,
                # while the completed consumer and prior stage receipts keep
                # their original m1 bytes. This is not a real large pack.
                packed_manifest['verified_at']='2026-11-13T16:59:00+08:00'
                with zipfile.ZipFile(option(argv,'--bundle'),'w',zipfile.ZIP_DEFLATED) as archive:
                    for path in sorted(consumer.rglob('*')):
                        if not path.is_file():
                            continue
                        name=path.relative_to(consumer).as_posix()
                        archive.writestr(name,case['api'].encode(packed_manifest)
                                         if name=='outputs/top100_delivery_manifest.json' else path.read_bytes())
                    archive.writestr('SOFTWARE_GATE_FIXTURE_ONLY.txt','Not a production whole-state archive.')
            elif label=='bootstrap-period':
                api.bootstrap_formal_candidate(consumer,'2026-11-13',option(argv,'--out'))
            elif label=='handoff':
                api.build(consumer,option(argv,'--current-checkpoint'),option(argv,'--candidate'),
                          '2026-11-13',option(argv,'--out'))
            elif label=='verify-handoff':
                target=option(argv,'--checkpoint')
                checked=case['native'][0].verify(target)
                body={role:case['native'][0].read_bound(checked,role) for role in api.ROLES}
                api.validate_recovery_payload(checked,body)
            else:
                raise AssertionError('Unexpected real driver stage '+label)
        except BaseException as exc:
            process.update(exit_code=1,error_type=type(exc).__name__,error=str(exc))
            result['stdout'].write_text('Explicit isolated software command.\n',encoding='utf-8')
            result['stderr'].write_text(str(exc),encoding='utf-8')
            result['process'].write_bytes(api.encode(process))
            raise
        result['stdout'].write_text('Explicit isolated software command; no market collection.\n',encoding='utf-8')
        result['stderr'].write_bytes(b'')
        result['process'].write_bytes(api.encode(process))
        return result
    config_raw=config.read_bytes()
    preparation=dict(consumer_config='consumer-config.json',scope_sha256='d'*64,
        files={'sources.zip':sha(b'explicit software source-bundle identity'),
               'consumer-config.json':sha(config_raw)})
    return driver.deliver_recovery(consumer=consumer,target='2026-11-13',consumer_sha=api.NATIVE,
        preparation=preparation,config=config,current_checkpoint=parent,out=out,recovery_plan=plan,run=run)


def _set_witness_calendar_transport(case,monkeypatch):
    original=subprocess.check_output
    def read(argv,*args,**kwargs):
        # Only the explicit native calendar child transport is replaced; Git
        # identity reads and every native model computation remain actual.
        if (len(argv)>6 and str(argv[1:3])==str(['-B','-c'])
                and 'from scripts.exchange_calendar import sessions_for_day' in str(argv[3])
                and 'print(json.dumps([str(x)[:10]' in str(argv[3])):
            first,last=str(argv[-2]),str(argv[-1])
            body=json.dumps([day for day in case['sessions'] if first<day<=last])
            return body if kwargs.get('text') else body.encode()
        return original(argv,*args,**kwargs)
    monkeypatch.setattr(subprocess,'check_output',read)
    return case


@pytest.fixture
def witness_calendar_transport(independently_built_recovery,monkeypatch):
    return _set_witness_calendar_transport(independently_built_recovery,monkeypatch)


def test_real_recovery_driver_resumes_after_check_and_checkpoint_verification_failures(witness_calendar_transport,tmp_path):
    case=witness_calendar_transport
    paths=_software_driver_paths(tmp_path/'original-job')
    shutil.copytree(case['data'],paths['consumer'])
    shutil.copytree(case['parent'].parent,paths['parent'].parent)
    paths['config'].parent.mkdir()
    paths['config'].write_bytes(case['api'].encode(dict(schema='explicit_software_consumer_config_only')))
    paths['plan'].write_bytes(case['api'].encode(dict(schema='microcap_scoped_recovery_plan_v1',
        target='2026-11-13',delivery_date='2026-11-13',required_rebalance_dates=['2026-11-12'],
        consumer_sha=case['api'].NATIVE,parent_not_after_date='2026-11-11')))
    with pytest.raises(RuntimeError,match='check'):
        _invoke_software_driver(case,paths,fail='check')
    assert not (paths['out']/'candidate').exists() and not (paths['out']/'handoff').exists()
    with pytest.raises(RuntimeError,match='verify-handoff'):
        _invoke_software_driver(case,paths,fail='verify-handoff')
    original_handoff=(paths['out']/'handoff/checkpoint.json').read_bytes()
    final=_invoke_software_driver(case,paths)
    counts,records=_stage_call_counts(paths['out'])
    assert counts=={'refresh':1,'check':2,'pack-formal':1,'bootstrap-period':1,'handoff':1,'verify-handoff':2}
    assert (paths['out']/'handoff/checkpoint.json').read_bytes()==original_handoff
    assert final['checkpoint_sha256']==sha(original_handoff)
    assert final['checkpoint']=='handoff/checkpoint.json' and final['paths_relative_to_result_directory'] is True
    assert all(row['explicit_software_command_runner'] is True for row in records)
    assert not (paths['out']/'stage-receipts/unknown.json').exists()
    out_env=os.environ.get('MICROCAP_RECOVERY_ACCEPTANCE_OUT')
    if out_env:
        state=Path(out_env)/'state'
        # The separately written report/CP fixture is retained as the report
        # example. The real coordinator's successful stage journal is added
        # verbatim so the next cloud run can actually resume these stages.
        state.mkdir(exist_ok=True)
        admission=importlib.import_module('restore_microcap_rebalance_recovery')
        producer='3fe2e15ee9e3f2ee7e25bbc328479f1fd9910d36'
        sealed=admission.seal(root=paths['out'].parent,runtime_sha=case['api'].NATIVE,
            producer_sha=producer,plan=paths['plan'],out=paths['out'].parent/admission.MANIFESTS['state'],
            phase='state',run_id=900000001,run_attempt=1,
            producer_commit=os.environ.get('GITHUB_SHA') or subprocess.check_output(
                ['git','-C',str(ROOT),'rev-parse','HEAD'],text=True,encoding='utf-8').strip())
        saved=state/'driver-state'
        saved.mkdir()
        for name in [admission.MANIFESTS['state'],*sealed['files']]:
            target=saved/name
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(paths['out'].parent/name,target)
        assert not any(name.startswith('scoped-consumer/') for name in sealed['files'])
        shutil.copy2(paths['out']/'stage-receipts/context.json',state/'source-journal.json')
        (state/'driver-call-counts.json').write_bytes(case['api'].encode(dict(counts=counts,
            derived_from_original_process_records=True,production_market_gates=False)))


def test_recovered_D_checkpoint_is_consumed_by_report_without_smtp(independently_built_recovery,tmp_path,monkeypatch):
    case=independently_built_recovery
    h,daily,_,_,_=case['native']
    checkpoint=case['recovered']/'checkpoint.json'
    result=daily.saved_close_result(checkpoint,'2026-11-13',tmp_path/'daily-result',
        now=datetime.fromisoformat('2026-11-13T17:00:00+08:00'))
    builder=importlib.import_module('build_microcap_member_digest')
    monkeypatch.setattr(builder,'now_bj',lambda:datetime.fromisoformat('2026-11-13T17:00:00+08:00'))
    # Match the production saved-close invocation: the source checkpoint is
    # original; the daily-result checkpoint has been reserialized by Native.
    monkeypatch.setattr(sys,'argv',['build-recovery-report','--result',str(tmp_path/'daily-result/result.json'),
        '--context-root',str(tmp_path/'daily-result'),'--context-checkpoint',str(checkpoint),
        '--expected-date','2026-11-13','--out',str(tmp_path/'rendered')])
    assert builder.main()==0
    metadata=json.loads((tmp_path/'rendered/metadata.json').read_bytes())
    assert metadata['status']=='OK'
    assert len(metadata['versions'])==3 and result['quote_coverage']=='100/100'
    assert result['collection_performed'] is False
    assert case['parent'].read_bytes()==case['parent_original']
    acceptance_out=os.environ.get('MICROCAP_RECOVERY_ACCEPTANCE_OUT')
    if acceptance_out:
        state=Path(acceptance_out)/'state'
        state.mkdir(exist_ok=True)
        for name,directory in [('parent',case['parent'].parent),('candidate',case['candidate'].parent),
                               ('formal-software',case['data']),('recovered',case['recovered'])]:
            shutil.copytree(directory,state/name)
        shutil.copytree(tmp_path/'rendered',state/'rendered-report')
        evidence=dict(scope='explicit_software_R_D_recovery_not_current_market_data',native_runtime=case['native_identity'],
            native_source_hashes=case['native_source_hashes'],seed_sha256=case['seed_sha256'],
            parent_history_original_bytes_preserved=True,actual_native_math_and_packaging=True,
            production_formal_gates_executed=False,target='2026-11-13',recovered_R=['2026-11-12'],
            report_status=metadata['status'],collection_performed=False,SMTP_calls=0,orders_placed=False)
        (state/'connected-recovery.json').write_bytes(case['api'].encode(evidence))
        trace=dict(schema='independent_software_recovery_execution_trace_v1',
            source_acquisition_calls=0,source_seed_reused=case['seed_sha256'],native_recovery_build_calls=1,
            recovered_checkpoint_sha256=sha(checkpoint.read_bytes()),software_only=True,
            production_driver_journal_exercised=True)
        (state/'recovery-execution-trace.json').write_bytes(case['api'].encode(trace))
        (state/'report-metadata.json').write_bytes(case['api'].encode(metadata))


def _admit_driver_state(saved, destination, native_root, monkeypatch):
    """Run the real sealed-state admission, modelling only Native large restore.

    The small software ZIP deliberately has Native pack's m2 clock. The real
    admission must recover the original m1 from the actual successful refresh
    sidecar before any original stage can be reused.
    """
    admission=importlib.import_module('restore_microcap_rebalance_recovery')
    saved=Path(saved)
    destination=Path(destination)
    paths=_software_driver_paths(destination)
    producer=destination/'scoped-source-state/runtime'
    paths['consumer'].mkdir(parents=True)
    producer.mkdir(parents=True)
    producer_source=Path(os.environ.get('MICROCAP_PRODUCER_ROOT',
        r'D:\Codex\microcap-local-maintenance\runtime')).resolve()
    expected_producer='3fe2e15ee9e3f2ee7e25bbc328479f1fd9910d36'
    real_read=subprocess.check_output
    assert real_read(['git','-C',str(producer_source),'rev-parse','HEAD'],text=True,encoding='utf-8').strip()==expected_producer
    assert real_read(['git','-C',str(native_root),'rev-parse','HEAD'],text=True,encoding='utf-8').strip()=='a7c1b9ac33454be4367880a0989dfc1466640503'
    observed=[]
    def actual_git_binding(argv,*args,**kwargs):
        if len(argv)>=5 and list(map(str,argv[:2]))==['git','-C']:
            argv=list(map(str,argv))
            if Path(argv[2]).resolve()==paths['consumer'].resolve():
                argv[2]=str(native_root)
            elif Path(argv[2]).resolve()==producer.resolve():
                argv[2]=str(producer_source)
        return real_read(argv,*args,**kwargs)
    def restore_native(label,argv):
        assert label=='restore-earned-formal-state'
        bundle=Path(argv[list(map(str,argv)).index('--bundle')+1])
        with zipfile.ZipFile(bundle) as archive:
            for name in archive.namelist():
                confined_name(name)
                target=paths['consumer']/name
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(archive.read(name))
        observed.append(sha((paths['consumer']/'outputs/top100_delivery_manifest.json').read_bytes()))
    with monkeypatch.context() as binding:
        binding.setattr(subprocess,'check_output',actual_git_binding)
        report=admission.admit_resume(saved/admission.MANIFESTS['state'],
            consumer=paths['consumer'],producer=producer,state=producer.parent,
            prepare_out=paths['config'].parent,delivery_out=paths['out'],
            target='2026-11-13',consumer_sha='a7c1b9ac33454be4367880a0989dfc1466640503',
            producer_sha=expected_producer,run=restore_native)
    assert report['original_json_and_clocks_rewritten'] is False
    assert report['original_formal_manifest_restored'] is True and len(observed)==1
    original=(saved/'scoped-delivery/formal-manifest-original.json').read_bytes()
    assert observed[0]!=sha(original)
    assert (paths['consumer']/'outputs/top100_delivery_manifest.json').read_bytes()==original
    sealed=json.loads((saved/admission.MANIFESTS['state']).read_bytes())
    for name,digest in sealed['files'].items():
        if name.startswith('scoped-previous/') or name=='scoped-recovery-plan.json':
            assert sha((saved/name).read_bytes())==digest
            admission._copy_private(saved/name,destination/name,destination)
    return paths,report


def test_recovered_artifact_is_consumed_by_current_recovery_and_report_code(tmp_path,monkeypatch):
    restored=os.environ.get('MICROCAP_RECOVERY_ACCEPTANCE_RESTORED')
    if restored is None:
        # Ordinary local pytest does not pretend to have run a cloud readback.
        assert os.environ.get('MICROCAP_RECOVERY_ACCEPTANCE_OUT') is None
        return
    state=Path(restored)
    api=importlib.import_module('microcap_recovery_handoff')
    monkeypatch.setattr(api,'datetime',SoftwareRecoveryClock)
    checkpoint=state/'recovered/checkpoint.json'
    document,payload=raw_package(checkpoint,checkpoint.parent)
    result=api.validate_recovery_payload(document,payload)
    assert result['target']=='2026-11-13'
    native_root=Path(os.environ['MICROCAP_NATIVE_ROOT']).resolve()
    assert api._runtime(native_root)==api.NATIVE
    h,daily,calendar_module,_,_=api._native(native_root)
    sessions=json.loads((native_root/'tests/fixtures/exchange_sessions_20261015.json').read_bytes())['sessions']
    monkeypatch.setattr(calendar_module,'sessions_for_day',lambda _:tuple(date.fromisoformat(day) for day in sessions))
    monkeypatch.setattr(calendar_module,'latest_completed_session',lambda *args,**kwargs:date(2026,11,13))
    case=dict(api=api,native=(h,daily,calendar_module,None,None),sessions=sessions)
    _set_witness_calendar_transport(case,monkeypatch)
    paths,admission=_admit_driver_state(state/'driver-state',tmp_path/'new-private-run',native_root,monkeypatch)
    before_processes=_stage_call_counts(paths['out'])
    result_original=(paths['out']/'result.json').read_bytes()
    resumed=_invoke_software_driver(case,paths,forbid_execution=True)
    assert resumed['checkpoint']=='handoff/checkpoint.json'
    assert (paths['out']/'result.json').read_bytes()==result_original
    assert _stage_call_counts(paths['out'])==before_processes
    journal_actual=json.loads((paths['out']/'stage-receipts/context.json').read_bytes())
    assert (state/'source-journal.json').read_bytes()==(paths['out']/'stage-receipts/context.json').read_bytes()
    saved=daily.saved_close_result(checkpoint,'2026-11-13',tmp_path/'readback-daily',
        now=datetime.fromisoformat('2026-11-13T17:00:00+08:00'))
    builder=importlib.import_module('build_microcap_member_digest')
    monkeypatch.setattr(builder,'now_bj',lambda:datetime.fromisoformat('2026-11-13T17:00:00+08:00'))
    context=builder.saved_context(tmp_path/'readback-daily',checkpoint_path=checkpoint,
        expected_date='2026-11-13',source_result=saved)
    report=builder.render(saved,expected_date='2026-11-13',context=context)
    assert report['status']=='OK' and saved['collection_performed'] is False
    contract=importlib.import_module('microcap_member_report_contract')
    csvs=contract.final_signal_files(saved,tmp_path/'readback-daily','2026-11-13',builder.REVISIONS,context=context)
    assert set(csvs)=={'v2.0','v2.3','v2.5'}
    journal=json.loads((state/'recovery-execution-trace.json').read_bytes())
    assert journal['native_recovery_build_calls']==1 and journal['source_acquisition_calls']==0
    assert journal['recovered_checkpoint_sha256']==sha(checkpoint.read_bytes())
    out=Path(os.environ['MICROCAP_RECOVERY_ACCEPTANCE_OUT'])/'evidence'
    out.mkdir()
    (out/'readback-consumption.json').write_bytes(api.encode(dict(consumed=True,
        target=result['target'],recovery_proof_revalidated=True,report_status=report['status'],
        production_driver_journal_exercised=True,all_successful_stages_reused=True,
        real_sealed_state_admission_exercised=True,
        original_m1_restored_after_distinct_pack_m2=admission['original_formal_manifest_restored'],
        original_json_and_clocks_rewritten=admission['original_json_and_clocks_rewritten'],
        three_final_csv_sha256={version:sha(raw) for version,raw in csvs.items()},
        successful_stage_context_sha256=sha((state/'source-journal.json').read_bytes()),
        source_journal_schema=journal_actual['schema'],original_stage_call_counts=before_processes[0],
        source_acquisition_calls=0,new_recovery_build_calls=0,SMTP_calls=0,orders_placed=False)))


def _acceptance_cli(argv=None):
    """Run software-only fault cases, then publish/read one dedicated artifact.

    The CLI never calls a production workflow, market collector or mailer.
    Actual GitHub writing is done only by the upload action in this workflow.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('produce', 'readback'))
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--scratch',type=Path,
                        help='Optional managed pytest scratch; cloud defaults to its ephemeral runner directory')
    parser.add_argument('--source-run', type=int)
    args = parser.parse_args(argv)
    if (args.phase == 'produce' and args.source_run is not None) or (args.phase == 'readback' and args.source_run is None):
        parser.error('Readback requires exactly one explicit previous source run')
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    head = exact_sha(os.environ.get('GITHUB_SHA') or subprocess.check_output(
        ['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True, encoding='utf-8').strip())
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        subprocess.run(['git', '-C', str(ROOT), 'diff', '--exit-code', 'HEAD', '--'], check=True)
    os.environ['MICROCAP_RECOVERY_ACCEPTANCE_OUT'] = str(out)
    readback = None
    if args.phase == 'readback':
        readback = restore_acceptance(args.source_run, head, out / 'restored-state')
        (out / 'readback-source.json').write_text(json.dumps(readback, indent=2) + '\n', encoding='utf-8')
        os.environ['MICROCAP_RECOVERY_ACCEPTANCE_RESTORED'] = str(out / 'restored-state')
        selection = 'test_recovered_artifact_is_consumed_by_current_recovery_and_report_code'
    else:
        selection = 'not test_recovered_artifact_is_consumed_by_current_recovery_and_report_code'
    scratch=args.scratch.resolve() if args.scratch is not None else out/'scratch'
    status = pytest.main([str(Path(__file__).resolve()), '-q', '-p', 'no:cacheprovider',
                          '--basetemp', str(scratch), '--junitxml', str(out / 'junit.xml'), '-k', selection])
    if status:
        return int(status)
    state = out / 'state'
    # These are written only by the actual cross-module recovery case below.
    # Transport-only tests can never earn a misleading successful producer.
    required = ('connected-recovery.json', 'recovered/checkpoint.json', 'source-journal.json')
    if args.phase == 'produce' and any(not (state / name).is_file() for name in required):
        raise ValueError('No complete isolated recovery execution was produced')
    if args.phase == 'produce':
        body = pack_state(state, head)
        (out / ARTIFACT_FILE).write_bytes(body)
        inner_sha = sha(body)
    else:
        result = out / 'evidence/readback-consumption.json'
        if not result.is_file() or json.loads(result.read_bytes()).get('consumed') is not True:
            raise ValueError('Restored artifact was not consumed by the recovery/report code')
        inner_sha = readback['inner_sha256']
    receipt = dict(schema='microcap_crossday_acceptance_execution_v1', phase=args.phase,
                   automation_head=head, success=True, software_observations=True,
                   current_signal_ready=False, production_refresh_all_or_check=False,
                   source_run=args.source_run, fallback_used=False if readback else None,
                   inner_sha256=inner_sha, SMTP_calls=0, orders_placed=False,
                   normal_member_namespace_written=False)
    (out / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(_acceptance_cli())
