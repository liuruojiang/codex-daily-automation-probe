"""Second-round state clock counterexamples; no market collection or sending."""
import io
import json
import zipfile

import pytest

from test_member_split_rebalance_audit import archive, install, REPO, SHA, restore


def with_execution_date(value, *, present=True):
    name, raw = archive(day='2026-11-09')
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        files = {p: z.read(p) for p in z.namelist()}
    checkpoint = json.loads(files['checkpoint.json'])
    if present:
        checkpoint['next_trade_date'] = value
    else:
        checkpoint.pop('next_trade_date', None)
    files['checkpoint.json'] = json.dumps(checkpoint).encode()
    target = io.BytesIO()
    with zipfile.ZipFile(target, 'w') as z:
        for path, body in files.items():
            z.writestr(path, body)
    return name, target.getvalue()


def test_same_close_different_execution_dates_are_not_latest_upload_wins(monkeypatch):
    install(monkeypatch, [(21, with_execution_date('2026-11-11')),
                          (20, with_execution_date('2026-11-10'))])
    with pytest.raises(RuntimeError, match='Conflicting'):
        restore.fetch_latest(REPO, 'isolated', SHA, '2026-11-09')


def test_same_close_missing_and_present_execution_dates_are_not_equivalent(monkeypatch):
    install(monkeypatch, [(21, with_execution_date(None, present=False)),
                          (20, with_execution_date('2026-11-10'))])
    with pytest.raises(RuntimeError, match='Conflicting'):
        restore.fetch_latest(REPO, 'isolated', SHA, '2026-11-09')


def test_equivalent_execution_dates_allow_latest_verified_upload(monkeypatch):
    pair = with_execution_date('2026-11-10')
    install(monkeypatch, [(21, pair), (20, pair)])
    assert restore.fetch_latest(REPO, 'isolated', SHA, '2026-11-09')['id'] == 21


def test_missing_execution_date_remains_legacy_compatible(monkeypatch):
    install(monkeypatch, [(20, with_execution_date(None, present=False))])
    assert restore.fetch_latest(REPO, 'isolated', SHA, '2026-11-09')['id'] == 20


@pytest.mark.parametrize('value', ['2026-11-09', '2026-11-06', '', '2026-11-10T00:00:00',
    '2026-11-10T00:00:00+08:00', '20261110', 'not-a-date', False, 20261110])
def test_execution_date_must_be_canonical_daily_date_after_anchor(value):
    raw = with_execution_date(value)[1]
    with pytest.raises(ValueError):
        restore.checkpoint_identity(raw)


def test_single_later_canonical_date_is_not_presented_as_calendar_certification(monkeypatch):
    # This transport check authenticates a state clock, not exchange-calendar
    # coverage. Native daily.build independently enforces the immediate session.
    install(monkeypatch, [(20, with_execution_date('2026-11-11'))])
    assert restore.fetch_latest(REPO, 'isolated', SHA, '2026-11-09')['id'] == 20
