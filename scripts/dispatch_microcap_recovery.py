"""One persistent, date-keyed recovery dispatch shared by local 09/11 slots.

No SMTP or daily dispatch. Ambiguous writes are reconciled by reads only,
including after a restart. GitHub authentication stays inside the existing gh.
"""
from __future__ import annotations
import argparse
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlencode

from prepare_microcap_local import atomic, encode, writer_lock

REPO = 'liuruojiang/codex-daily-automation-probe'
WORKFLOW = 'microcap-member-maintenance.yml'
REVISION = 'microcap-recovery-v1'
ACTIVE = ('queued', 'in_progress', 'waiting', 'pending', 'requested')
FAILED = ('failure', 'cancelled', 'timed_out')


class ReadFailure(RuntimeError):
    pass


class Rejected(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__('dispatch_explicitly_rejected_' + str(status))


def gh_api(method, path, payload=None):
    command = ['gh', 'api', '--method', method, path]
    if payload is not None:
        command += ['--input', '-']
    try:
        result = subprocess.run(command, input=json.dumps(payload).encode() if payload is not None else None,
            capture_output=True, timeout=45)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReadFailure('github_transport_unconfirmed') from exc
    if result.returncode:
        match = re.search(rb'\(HTTP ([0-9]{3})\)', result.stderr)
        status = int(match[1]) if match else None
        if method == 'POST' and status in (400, 401, 403, 404, 409, 410, 422, 429):
            raise Rejected(status)
        raise ReadFailure('github_response_unconfirmed_' + str(status or 'transport'))
    if not result.stdout.strip():
        if method == 'POST':
            return None
        raise ReadFailure('github_empty_read_response')
    try:
        return json.loads(result.stdout)
    except (ValueError, UnicodeError) as exc:
        raise ReadFailure('github_invalid_json_response') from exc


def source_probe(root, day, pin, calendar_hash):
    code = r'''
from pathlib import Path
import hashlib,subprocess,sys
root=Path(sys.argv[1]).resolve();day,pin,expected=sys.argv[2:]
actual=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],encoding='utf-8').strip()
if actual!=pin:raise ValueError('source_runtime_role_differs')
raw=(root/'scripts/exchange_calendar.py').read_bytes().replace(b'\r\n',b'\n')
if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('source_calendar_code_differs')
sys.path.insert(0,str(root))
from scripts.exchange_calendar import latest_completed_session
if latest_completed_session().isoformat()!=day:raise ValueError('source_latest_completed_session_differs')
'''
    result = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', code, str(root), day, pin, calendar_hash],
                            capture_output=True, timeout=120)
    if result.returncode:
        raise ReadFailure('source_calendar_or_git_role_verification_failed')


def _config(api, repository, head=None):
    base = 'repos/' + repository
    head = head or api('GET', base + '/commits/main')['sha']
    if re.fullmatch('[0-9a-f]{40}', head or '') is None:
        raise ReadFailure('remote_main_identity_invalid')
    def read(name):
        obj = api('GET', base + '/contents/config/' + name + '?ref=' + head)
        return json.loads(base64.b64decode(obj['content']))
    host = read('microcap_local_maintenance.json'); scoped = read('microcap_scoped_rebalance.json')
    producer = read('microcap_local_producer_runtime.json')
    if (host.get('schema') != 'microcap_local_maintenance_host_v1' or host.get('owner') != 'local'
            or scoped.get('schema') != 'microcap_scoped_rebalance_runtime_v1' or scoped.get('enabled') is not True
            or producer.get('schema') != 'microcap_local_source_runtime_v1'
            or producer.get('role') != 'source_preparation_only' or scoped.get('producer_sha') != producer.get('runtime_sha')
            or any(re.fullmatch('[0-9a-f]{40}', value or '') is None for value in (scoped.get('runtime_sha'), producer.get('runtime_sha')))):
        raise ReadFailure('remote_recovery_owner_or_runtime_contract_differs')
    calendar_hash = producer.get('calculation_sources', {}).get('scripts/exchange_calendar.py')
    if re.fullmatch('[0-9a-f]{64}', calendar_hash or '') is None:
        raise ReadFailure('remote_source_calendar_binding_invalid')
    contract = dict(owner=host['owner'], enabled=scoped['enabled'], consumer_sha=scoped['runtime_sha'],
        producer_sha=producer['runtime_sha'], calendar_sha256=calendar_hash,
        host_schema=host['schema'], scoped_schema=scoped['schema'], producer_schema=producer['schema'],
        producer_role=producer['role'])
    return head, contract


def _runs(api, repository, **query):
    result = []
    for page in range(1, 4):
        params = urlencode(dict(query, per_page=100, page=page))
        obj = api('GET', f'repos/{repository}/actions/workflows/{WORKFLOW}/runs?' + params)
        rows = obj.get('workflow_runs') if isinstance(obj, dict) else None
        if (not isinstance(rows, list) or type(obj.get('total_count')) is not int
                or obj['total_count'] < len(rows)):
            raise ReadFailure('workflow_run_page_invalid')
        for row in rows:
            if (type(row.get('id')) is not int or row['id'] <= 0
                    or str(row.get('path', '')).split('@')[0] != '.github/workflows/' + WORKFLOW
                    or row.get('status') not in (*ACTIVE, 'completed')
                    or re.fullmatch('[0-9a-f]{40}', row.get('head_sha', '')) is None
                    or not isinstance(row.get('created_at'), str)
                    or re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z', row['created_at']) is None
                    or ('branch' in query and row.get('head_branch') != query['branch'])
                    or ('event' in query and row.get('event') != query['event'])
                    or ('status' in query and row['status'] != query['status'])):
                raise ReadFailure('workflow_run_identity_or_status_invalid')
            try:
                datetime.strptime(row['created_at'], '%Y-%m-%dT%H:%M:%SZ')
            except ValueError as exc:
                raise ReadFailure('workflow_run_clock_invalid') from exc
        result += rows
        if len({row['id'] for row in result}) != len(result):
            raise ReadFailure('workflow_run_page_overlap')
        if len(rows) < 100:
            if obj['total_count'] > len(result):
                raise ReadFailure('workflow_run_page_incomplete')
            return result
    raise ReadFailure('workflow_run_read_budget_exhausted')


def dispatch(root, day, state_root, repository=REPO, *, api=gh_api, probe=source_probe, now=None):
    if datetime.fromisoformat(day).date().isoformat() != day or not re.fullmatch(r'[\w.-]+/[\w.-]+', repository):
        raise ValueError('Canonical target/repository required')
    state_root = Path(state_root).resolve(); state_root.mkdir(parents=True, exist_ok=True)
    path = state_root / ('recovery-' + day + '.json')
    key = REVISION + '-' + day; title = 'microcap-dispatch:' + key
    clock = now or (lambda: datetime.now(timezone.utc))
    with writer_lock(state_root / 'dispatch.lock'):
        head, runtime_contract = _config(api, repository)
        probe(Path(root).resolve(), day, runtime_contract['producer_sha'], runtime_contract['calendar_sha256'])
        record = json.loads(path.read_bytes()) if path.exists() else None
        if record and (record.get('schema') != REVISION or record.get('target') != day
                       or record.get('repository') != repository or record.get('key') != key):
            raise ReadFailure('preserve_unrecognized_dispatch_journal')
        if record and record.get('status') not in ('intent', 'unknown_pending', 'accepted_pending', 'already_succeeded', 'failed', 'rejected'):
            raise ReadFailure('preserve_unknown_dispatch_journal_state')
        if record and record.get('status') == 'already_succeeded':
            return record
        def save(value):
            atomic(path, encode(value)); return value
        slots = [r for r in _runs(api, repository, branch='main', event='workflow_dispatch', created='>=' + day)
                 if r.get('display_title') == title]
        def reconcile(journal):
            matches = [r for r in slots if r['id'] > journal['after_run_id']
                       and r.get('created_at', '') >= journal['created_after']]
            if len(matches) > 1:
                raise ReadFailure('multiple_runs_for_one_dispatch_intent')
            if not matches:
                return save(dict(journal, status='accepted_pending' if journal['status'].startswith('accepted') else 'unknown_pending'))
            run = matches[0]
            if run['head_sha'] != journal['expected_head']:
                frozen = journal.get('runtime_contract')
                if not isinstance(frozen, dict):
                    raise ReadFailure('preserve_legacy_intent_without_frozen_head_contract')
                actual_head, actual_contract = _config(api, repository, run['head_sha'])
                if actual_contract != frozen:
                    raise ReadFailure('dispatched_run_runtime_contract_differs')
                journal = dict(journal, observed_head=actual_head, head_advance_reconciled=True,
                    head_advance_evidence=dict(configuration_ref=actual_head, runtime_contract=actual_contract))
            if run['status'] != 'completed':
                return save(dict(journal, status='accepted_pending', run_id=run['id']))
            if run.get('conclusion') not in (*FAILED, 'success'):
                raise ReadFailure('dispatch_run_terminal_state_unknown')
            return save(dict(journal, status='failed' if run['conclusion'] in FAILED else 'already_succeeded',
                             run_id=run['id'], conclusion=run['conclusion']))
        if record and record.get('status') in ('intent', 'unknown_pending', 'accepted_pending'):
            record = reconcile(record)
            if record['status'] != 'failed':
                return record
        successes = [r for r in slots if r['status'] == 'completed' and r.get('conclusion') == 'success']
        if successes:
            return save(dict(schema=REVISION, target=day, repository=repository, key=key, status='already_succeeded',
                             run_id=max(r['id'] for r in successes), history=[record] if record else []))
        for status in ACTIVE:
            active = _runs(api, repository, status=status)
            if any(r['status'] != 'completed' for r in active):
                return dict(schema=REVISION, target=day, repository=repository, key=key, status='await_existing_workflow', run_ids=[r['id'] for r in active])
        stamp = clock().astimezone(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
        journal = dict(schema=REVISION, target=day, repository=repository, key=key, status='intent',
            expected_head=head, runtime_contract=runtime_contract, created_after=stamp,
            after_run_id=max([r['id'] for r in slots] or [0]),
            history=[record] if record else [])
        save(journal)
        try:
            api('POST', f'repos/{repository}/actions/workflows/{WORKFLOW}/dispatches',
                dict(ref='main', inputs=dict(task='rebalance_recovery', dispatch_key=key)))
            journal['status'] = 'accepted_pending'
        except Rejected as exc:
            return save(dict(journal, status='rejected', http_status=exc.status))
        except Exception:
            journal['status'] = 'unknown_pending'
        save(journal)
        slots = [r for r in _runs(api, repository, branch='main', event='workflow_dispatch', created='>=' + day)
                 if r.get('display_title') == title]
        return reconcile(journal)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True); parser.add_argument('--date', required=True)
    parser.add_argument('--state-root', type=Path, required=True); parser.add_argument('--repository', default=REPO)
    args = parser.parse_args(argv)
    try:
        value = dispatch(args.root, args.date, args.state_root, args.repository)
    except Exception as exc:
        print(json.dumps(dict(status='blocked', reason=type(exc).__name__ + ':' + str(exc)), ensure_ascii=False)); return 1
    print(json.dumps(value, ensure_ascii=False))
    return 1 if value['status'] == 'rejected' else 0


if __name__ == '__main__':
    raise SystemExit(main())
