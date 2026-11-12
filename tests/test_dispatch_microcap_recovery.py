"""No live POST: independent in-memory GitHub and source-date probe."""
import base64
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import dispatch_microcap_recovery as m

DAY = '2026-11-13'
HEAD = 'a' * 40
PIN = 'b' * 40
NOW = datetime.fromisoformat('2026-11-16T09:00:00+00:00')


class Github:
    def __init__(self):
        self.head = HEAD; self.runs = []; self.posts = []; self.on_post = None
        self.reads = []; self.config_by_head = {}
        self.block_reads = False; self.bad_page = False
        self.config = {
            'microcap_local_maintenance.json': dict(schema='microcap_local_maintenance_host_v1', owner='local'),
            'microcap_scoped_rebalance.json': dict(schema='microcap_scoped_rebalance_runtime_v1', enabled=True, runtime_sha='c' * 40, producer_sha=PIN),
            'microcap_local_producer_runtime.json': dict(schema='microcap_local_source_runtime_v1', role='source_preparation_only', runtime_sha=PIN,
                calculation_sources={'scripts/exchange_calendar.py': 'd' * 64}),
        }

    def run(self, number, status='queued', conclusion=None, **change):
        value = dict(id=number, path='.github/workflows/' + m.WORKFLOW, head_sha=self.head,
            status=status, conclusion=conclusion, head_branch='main', event='workflow_dispatch',
            created_at='2026-11-16T09:00:00Z', display_title='microcap-dispatch:' + m.REVISION + '-' + DAY)
        value.update(change); self.runs.append(value); return value

    def __call__(self, method, path, payload=None):
        if method == 'POST':
            self.posts.append((path, payload))
            if self.on_post: return self.on_post(self)
            return None
        self.reads.append(path)
        if self.block_reads: raise m.ReadFailure('github_response_unconfirmed_403')
        if path.endswith('/commits/main'): return dict(sha=self.head)
        if '/contents/config/' in path:
            name = path.split('/contents/config/', 1)[1].split('?', 1)[0]
            ref = parse_qs(urlsplit('https://api.github.com/' + path).query)['ref'][0]
            config = self.config_by_head.get(ref, self.config)
            return dict(content=base64.b64encode(json.dumps(config[name]).encode()).decode())
        query = parse_qs(urlsplit('https://api.github.com/' + path).query)
        if self.bad_page: return dict(total_count=1, workflow_runs=[])
        rows = [r for r in self.runs if 'status' not in query or r['status'] == query['status'][0]]
        return dict(total_count=len(rows), workflow_runs=rows)


def call(tmp_path, github, **options):
    def probe(root, day, pin, calendar_hash):
        assert day == DAY and pin == PIN and calendar_hash == 'd' * 64
    return m.dispatch(tmp_path / 'source-readonly', DAY, tmp_path / 'receipts', api=github,
                      probe=options.pop('probe', probe), now=lambda: NOW, **options)


def test_09_and_11_share_exact_slot_and_never_queue_twice(tmp_path):
    api = Github(); api.on_post = lambda gh: gh.run(10)
    first = call(tmp_path, api); second = call(tmp_path, api)
    assert first['status'] == second['status'] == 'accepted_pending'
    assert len(api.posts) == 1
    assert api.posts[0][1] == dict(ref='main', inputs=dict(task='rebalance_recovery', dispatch_key=m.REVISION + '-' + DAY))
    assert first['key'] == second['key'] and first['run_id'] == 10


def test_lost_response_reconciles_actual_run_without_post_retry(tmp_path):
    api = Github()
    def accepted_then_lost(gh):
        gh.run(20); raise ConnectionError('response lost after acceptance')
    api.on_post = accepted_then_lost
    assert call(tmp_path, api)['status'] == 'accepted_pending'
    assert call(tmp_path, api)['run_id'] == 20
    assert len(api.posts) == 1


def test_restart_with_unknown_result_is_read_only_even_after_head_moves(tmp_path):
    api = Github(); api.on_post = lambda _: (_ for _ in ()).throw(TimeoutError())
    assert call(tmp_path, api)['status'] == 'unknown_pending'
    api.head = 'e' * 40
    assert call(tmp_path, api)['status'] == 'unknown_pending'
    assert len(api.posts) == 1


def test_unknown_write_does_not_borrow_success_with_different_runtime_contract(tmp_path):
    api = Github(); api.on_post = lambda _: (_ for _ in ()).throw(TimeoutError())
    call(tmp_path, api)
    changed = deepcopy(api.config)
    changed['microcap_scoped_rebalance.json']['runtime_sha'] = 'f' * 40
    api.config_by_head['e' * 40] = changed
    api.run(30, status='completed', conclusion='success', head_sha='e' * 40)
    with pytest.raises(m.ReadFailure, match='runtime_contract_differs'): call(tmp_path, api)
    assert len(api.posts) == 1


def test_confirmed_success_remains_noop_after_run_metadata_disappears(tmp_path):
    api = Github(); api.run(31, status='completed', conclusion='success')
    assert call(tmp_path, api)['status'] == 'already_succeeded'
    api.runs.clear(); api.head = 'e' * 40
    assert call(tmp_path, api)['status'] == 'already_succeeded'
    assert api.posts == []


def test_failed_same_key_may_retry_once_using_same_key(tmp_path):
    api = Github(); api.on_post = lambda gh: gh.run(40)
    call(tmp_path, api)
    api.runs[0].update(status='completed', conclusion='failure')
    api.on_post = lambda gh: gh.run(41)
    result = call(tmp_path, api)
    assert result['run_id'] == 41 and len(api.posts) == 2
    assert api.posts[0][1]['inputs'] == api.posts[1][1]['inputs']


@pytest.mark.parametrize('status', m.ACTIVE)
def test_any_active_same_workflow_is_awaited_without_dispatch(tmp_path, status):
    api = Github(); api.run(50, status=status, display_title='unrelated accepted slot')
    assert call(tmp_path, api)['status'] == 'await_existing_workflow'
    assert api.posts == []


@pytest.mark.parametrize('fault', ['403', 'bad_page', 'owner', 'runtime', 'source_probe'])
def test_read_or_role_failure_never_becomes_empty_success(tmp_path, fault):
    api = Github(); options = {}
    if fault == '403': api.block_reads = True
    elif fault == 'bad_page': api.bad_page = True
    elif fault == 'owner': api.config['microcap_local_maintenance.json']['owner'] = 'cloud'
    elif fault == 'runtime': api.config['microcap_scoped_rebalance.json']['producer_sha'] = 'f' * 40
    else: options['probe'] = lambda *a: (_ for _ in ()).throw(m.ReadFailure('wrong real completed day'))
    with pytest.raises(m.ReadFailure): call(tmp_path, api, **options)
    assert api.posts == []


def test_explicit_rejection_is_recorded_without_immediate_retry(tmp_path):
    api = Github(); api.on_post = lambda _: (_ for _ in ()).throw(m.Rejected(429))
    result = call(tmp_path, api)
    assert result['status'] == 'rejected' and result['http_status'] == 429 and len(api.posts) == 1


def test_get_failure_after_unknown_write_retains_intent_on_disk(tmp_path):
    api = Github()
    def lost(gh):
        gh.block_reads = True; raise TimeoutError()
    api.on_post = lost
    with pytest.raises(m.ReadFailure): call(tmp_path, api)
    journal = json.loads((tmp_path / 'receipts' / ('recovery-' + DAY + '.json')).read_bytes())
    assert journal['status'] == 'unknown_pending'
    api.block_reads = False; api.on_post = None
    assert call(tmp_path, api)['status'] == 'unknown_pending' and len(api.posts) == 1


def test_unrecognized_journal_cannot_reset_unknown_write(tmp_path):
    api = Github(); folder = tmp_path / 'receipts'; folder.mkdir()
    path = folder / ('recovery-' + DAY + '.json')
    raw = json.dumps(dict(schema=m.REVISION, repository=m.REPO, target=DAY, key=m.REVISION + '-' + DAY, status='force_retry')).encode()
    path.write_bytes(raw)
    with pytest.raises(m.ReadFailure): call(tmp_path, api)
    assert path.read_bytes() == raw and api.posts == []


@pytest.mark.parametrize('timestamp', ['unconfirmedZ', '2026-19-16T09:00:00Z'])
def test_unknown_run_clock_does_not_authorize_dispatch(tmp_path, timestamp):
    api = Github(); api.run(70, created_at=timestamp)
    with pytest.raises(m.ReadFailure): call(tmp_path, api)
    assert api.posts == []


def test_pending_intent_cannot_adopt_pre_intent_run_even_with_same_key(tmp_path):
    api = Github(); api.on_post = lambda _: (_ for _ in ()).throw(TimeoutError())
    call(tmp_path, api)
    api.run(80, status='completed', conclusion='success', created_at='2026-11-16T08:59:59Z')
    assert call(tmp_path, api)['status'] == 'unknown_pending' and len(api.posts) == 1


def test_os_lock_denies_second_dispatch_writer_before_any_post(tmp_path):
    api = Github()
    with m.writer_lock(tmp_path / 'receipts' / 'dispatch.lock'):
        with pytest.raises(OSError): call(tmp_path, api)
    assert api.posts == []


def test_overlapping_pages_are_unknown_not_empty(tmp_path):
    api = Github()
    for number in range(1, 101): api.run(number)
    with pytest.raises(m.ReadFailure, match='page_overlap'): call(tmp_path, api)
    assert api.posts == []


def test_main_advance_between_read_and_post_adopts_unique_accepted_success(tmp_path):
    api = Github()
    def accepted_at_new_head(gh):
        gh.head = 'e' * 40
        gh.run(90, status='completed', conclusion='success')
    api.on_post = accepted_at_new_head
    result = call(tmp_path, api)
    assert result['status'] == 'already_succeeded'
    assert result['expected_head'] == HEAD and result['observed_head'] == 'e' * 40
    assert result['head_advance_reconciled'] is True
    assert result['head_advance_evidence']['configuration_ref'] == 'e' * 40
    assert any('/contents/config/' in p and p.endswith('?ref=' + 'e' * 40) for p in api.reads)
    assert len(api.posts) == 1 and call(tmp_path, api)['status'] == 'already_succeeded'


def test_reconciliation_checks_run_head_not_mutable_current_main(tmp_path):
    api = Github(); api.on_post = lambda _: (_ for _ in ()).throw(TimeoutError())
    call(tmp_path, api)
    changed = deepcopy(api.config)
    changed['microcap_local_producer_runtime.json']['calculation_sources']['scripts/exchange_calendar.py'] = 'f' * 64
    api.config_by_head['e' * 40] = changed
    api.run(91, status='completed', conclusion='success', head_sha='e' * 40)
    api.head = 'c' * 40
    with pytest.raises(m.ReadFailure, match='runtime_contract_differs'): call(tmp_path, api)
    assert len(api.posts) == 1


def test_two_post_intent_runs_are_ambiguous_even_if_one_head_matches(tmp_path):
    api = Github(); api.on_post = lambda _: (_ for _ in ()).throw(TimeoutError())
    call(tmp_path, api)
    api.run(92, status='completed', conclusion='success')
    api.run(93, status='completed', conclusion='success', head_sha='e' * 40)
    with pytest.raises(m.ReadFailure, match='multiple_runs'): call(tmp_path, api)
    assert len(api.posts) == 1


@pytest.mark.parametrize('advanced', [False, True])
def test_legacy_intent_only_adopts_its_exact_expected_head(tmp_path, advanced):
    api = Github(); api.on_post = lambda _: (_ for _ in ()).throw(TimeoutError())
    call(tmp_path, api)
    path = tmp_path / 'receipts' / ('recovery-' + DAY + '.json')
    journal = json.loads(path.read_bytes()); del journal['runtime_contract']
    path.write_text(json.dumps(journal), encoding='utf-8')
    api.run(94, status='completed', conclusion='success', head_sha='e' * 40 if advanced else HEAD)
    if advanced:
        with pytest.raises(m.ReadFailure, match='legacy_intent'): call(tmp_path, api)
        assert json.loads(path.read_bytes())['status'] == 'unknown_pending'
    else:
        assert call(tmp_path, api)['status'] == 'already_succeeded'
    assert len(api.posts) == 1


def test_advanced_head_confirmed_failure_allows_one_next_call_retry(tmp_path):
    api = Github()
    def failed_at_new_head(gh):
        gh.head = 'e' * 40
        gh.run(95, status='completed', conclusion='failure')
    api.on_post = failed_at_new_head
    result = call(tmp_path, api)
    assert result['status'] == 'failed' and result['head_advance_reconciled'] is True
    api.on_post = lambda gh: gh.run(96)
    assert call(tmp_path, api)['run_id'] == 96 and len(api.posts) == 2
    assert api.posts[0][1]['inputs'] == api.posts[1][1]['inputs']
