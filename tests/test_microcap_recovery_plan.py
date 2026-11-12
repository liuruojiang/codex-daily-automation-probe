"""Missed-period decisions use native-calendar observations, never fake clocks."""
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import microcap_recovery_plan as planner


RUNTIME = 'a7c1b9ac33454be4367880a0989dfc1466640503'
SESSIONS = ['2026-10-29', '2026-10-30', '2026-11-02', '2026-11-03', '2026-11-04',
            '2026-11-05', '2026-11-06', '2026-11-09', '2026-11-10', '2026-11-11',
            '2026-11-12', '2026-11-13', '2026-11-16', '2026-11-17', '2026-11-18',
            '2026-11-19', '2026-11-20', '2026-11-23', '2026-11-24', '2026-11-25',
            '2026-11-26', '2026-11-27', '2026-11-30']
REBALANCES = ['2026-10-29', '2026-11-12', '2026-11-26']


def observation(*, completed='2026-11-13', anchor='2026-10-29', asof='2026-11-11', phase='current'):
    return dict(consumer_sha=RUNTIME, latest_completed_session=completed, sessions=SESSIONS,
                rebalance_dates=REBALANCES, checkpoint_path='verified-small/checkpoint.json',
                checkpoint_sha256='b' * 64, checkpoint_as_of_date=asof,
                checkpoint_period_status=phase, checkpoint_period_anchor=anchor,
                checkpoint_period_sha256='c' * 64, checkpoint_members=100,
                native_checkpoint_verified=True)


def plan(value=None, *, task='rebalance_recovery'):
    value = value or observation()
    return planner.build_plan(value, value['latest_completed_session'], RUNTIME, task=task)


def test_failed_R_on_later_non_R_retains_original_R_and_latest_D():
    value = plan()
    assert value['run'] is True
    assert value['target'] == value['delivery_date'] == '2026-11-13'
    assert value['recovery_R'] == '2026-11-12'
    assert value['required_rebalance_dates'] == ['2026-11-12']
    assert value['parent_not_after_date'] == '2026-11-11'
    assert value['recovery_next_session'] == '2026-11-13'
    assert value['latest_completed_session'] != value['recovery_R']
    assert value['email_sent'] is value['orders_placed'] is False


def test_latest_D_awaiting_reference_is_not_used_as_original_R_parent():
    value = plan(observation(asof='2026-11-13', phase='awaiting_handoff'))
    assert value['run'] is True and value['needs_checkpoint_advance'] is False
    assert value['latest_checkpoint_as_of_date'] == '2026-11-13'
    assert value['parent_not_after_date'] == '2026-11-11'
    assert value['formal_parent_is_latest_checkpoint'] is False


def test_multiple_missed_periods_are_not_silently_reduced_to_last_R():
    value = plan(observation(completed='2026-11-27', asof='2026-11-27', phase='awaiting_handoff'))
    assert value['required_rebalance_dates'] == ['2026-11-12', '2026-11-26']
    assert value['recovery_R'] == '2026-11-12' and value['last_required_R'] == '2026-11-26'
    assert value['parent_not_after_date'] == '2026-11-11'


@pytest.mark.parametrize('task', ['rebalance', 'rebalance_recovery'])
def test_current_exact_D_and_latest_period_is_verified_noop(task):
    value = plan(observation(anchor='2026-11-12', asof='2026-11-13'), task=task)
    assert value['run'] is False and value['action'] == 'noop'
    assert value['required_rebalance_dates'] == []
    assert value['native_formal_gates_required'] is False


def test_explicit_recovery_catches_up_a_missing_daily_without_missing_R():
    value = plan(observation(anchor='2026-11-12', asof='2026-11-12'))
    assert value['run'] is True and value['action'] == 'daily_catchup'
    assert value['required_rebalance_dates'] == [] and value['recovery_R'] is None
    assert value['parent_not_after_date'] == '2026-11-12'
    assert value['formal_parent_is_latest_checkpoint'] is True


def test_normal_1510_does_not_become_a_daily_300_recompute():
    value = plan(observation(anchor='2026-11-12', asof='2026-11-12'), task='rebalance')
    assert value['run'] is False and value['needs_checkpoint_advance'] is True
    assert value['scheduled_task'] == 'rebalance'


def test_same_original_R_is_recovered_before_next_day_as_usual():
    value = plan(observation(completed='2026-11-12'))
    assert value['run'] is True and value['required_rebalance_dates'] == ['2026-11-12']


@pytest.mark.parametrize('field,bad', [
    ('consumer_sha', '0' * 40), ('native_checkpoint_verified', False),
    ('native_checkpoint_verified', 'true'), ('latest_completed_session', '2026-11-12'),
    ('checkpoint_sha256', 'bad'), ('checkpoint_period_sha256', '0' * 63),
    ('checkpoint_period_status', 'partial'), ('checkpoint_members', 99),
    ('checkpoint_members', True), ('checkpoint_as_of_date', '2026-11-16'),
    ('checkpoint_as_of_date', '2026-11-14'), ('checkpoint_period_anchor', '2026-11-11'),
    ('checkpoint_period_anchor', '2026-11-26'), ('checkpoint_path', ''),
])
def test_unverified_future_or_unbound_observation_cannot_become_a_plan(field, bad):
    item = observation(); item[field] = bad
    with pytest.raises((ValueError, TypeError)):
        planner.build_plan(item, '2026-11-13', RUNTIME)


@pytest.mark.parametrize('field,bad', [
    ('sessions', SESSIONS + [SESSIONS[-1]]),
    ('sessions', list(reversed(SESSIONS))), ('sessions', []),
    ('rebalance_dates', ['2026-11-14']), ('rebalance_dates', []),
    ('rebalance_dates', REBALANCES + [REBALANCES[-1]]),
])
def test_calendar_must_be_complete_sorted_unique_native_sessions(field, bad):
    item = observation(); item[field] = bad
    with pytest.raises(ValueError):
        plan(item)


def test_corrupt_awaiting_flag_cannot_turn_into_successful_noop():
    with pytest.raises(ValueError, match='no missing native period'):
        plan(observation(anchor='2026-11-12', asof='2026-11-13', phase='awaiting_handoff'))


def test_plan_decision_does_not_mutate_original_checkpoint_observation():
    item = observation(); original = copy.deepcopy(item)
    assert plan(item) == plan(item)
    assert item == original


def test_saved_plan_is_exact_immutable_object_not_mtime_cache(tmp_path):
    item = plan(); path = tmp_path / 'plan.json'
    first = planner.write_plan(path, item)
    assert planner.write_plan(path, item) == first
    changed = dict(item, consumer_sha='f' * 40)
    with pytest.raises(ValueError, match='another immutable plan'):
        planner.write_plan(path, changed)
    assert json.loads(path.read_bytes()) == item
