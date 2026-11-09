from datetime import date, datetime
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import microcap_local_due as due
import maintain_microcap_local as local

SESSIONS = tuple(date(2026, 11, n) for n in (5, 6, 9, 10))


@pytest.mark.parametrize('clock', ['2026-11-09T09:00:00+08:00',
                                  '2026-11-09T11:00:00+08:00',
                                  '2026-11-09T16:00:00+08:00'])
def test_late_start_preserves_morning_previous_friday_target(clock):
    assert due.morning_target(datetime.fromisoformat(clock), SESSIONS) == '2026-11-06'


@pytest.mark.parametrize('clock', ['2026-11-09T08:59:59+08:00', '2026-11-08T10:00:00+08:00'])
def test_no_early_or_non_session_collection(clock):
    assert due.morning_target(datetime.fromisoformat(clock), SESSIONS) is None


def test_tuesday_catches_up_to_monday_not_friday(tmp_path):
    p = due.plan(datetime.fromisoformat('2026-11-10T10:00:00+08:00'), SESSIONS, tmp_path, tmp_path)
    assert p['status'] == 'due' and p['target'] == '2026-11-09'
    assert p['automatic_on_wakeup'] is False


def test_count_only_completion_is_not_accepted(tmp_path):
    (tmp_path / 'result.json').write_bytes(local.encode(dict(status='complete', target='2026-11-06',
        total=300, complete=300, failures={}, notice_status='complete', metadata_status='complete', context={})))
    assert not due.completed(tmp_path, tmp_path, '2026-11-06')


def test_true_completed_receipts_skip_collection_but_changed_bytes_do_not(tmp_path):
    root = tmp_path / 'runtime';root.mkdir()
    (root / 'source').write_bytes(b'verified evidence')
    directory = tmp_path / 'runs' / '2026-11-06';directory.mkdir(parents=True)
    symbols = [f'{n:06d}' for n in range(1, 301)]
    context = {'date': '2026-11-06'}
    record = dict(status='complete', context=context, files=local.hashes(root, ['source']))
    for key in ['notice', 'metadata', 'current-names', *[s + '-' + stage for s in symbols for stage in ('price', 'share', 'ratio')]]:
        (directory / (key + '.json')).write_bytes(local.encode(record))
    result = dict(status='complete', target='2026-11-06', total=300, complete=300,
                  failures={}, notice_status='complete', metadata_status='complete',current_names_status='complete', context=context, symbols=symbols)
    (directory / 'result.json').write_bytes(local.encode(result))
    now = datetime.fromisoformat('2026-11-09T10:00:00+08:00')
    assert due.plan(now, SESSIONS, tmp_path, root)['status'] == 'already_complete'
    (root / 'source').write_bytes(b'changed')
    assert due.plan(now, SESSIONS, tmp_path, root)['status'] != 'already_complete'
