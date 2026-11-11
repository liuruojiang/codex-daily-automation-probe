"""Legacy C1/C2 reachability: archive recovery stays usable, all mail stays retired.

These tests evaluate the actual workflow graph with isolated inputs. They never
dispatch a job, open a market-data connection, or call an SMTP sender.
"""
import itertools
from pathlib import Path
import socket
import sys
import urllib.request

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from test_microcap_whole_hit_normal_chain import condition


@pytest.fixture(autouse=True)
def no_real_network_or_smtp(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError('Retirement test forbids real network/SMTP')
    monkeypatch.setattr(socket, 'create_connection', reject)
    monkeypatch.setattr(socket.socket, 'connect', reject)
    monkeypatch.setattr(socket.socket, 'connect_ex', reject)
    monkeypatch.setattr(urllib.request, 'urlopen', reject)
    for key in ('MAIL_SERVER', 'MAIL_PORT', 'MAIL_USERNAME', 'MAIL_PASSWORD', 'MAIL_FROM', 'MAIL_TO'):
        monkeypatch.delenv(key, raising=False)


def workflow():
    return yaml.safe_load((ROOT / '.github/workflows/microcap-realtime-digest.yml').read_text(encoding='utf8'))


def values(**changes):
    value = {
        'github.event_name': 'workflow_dispatch',
        'inputs.validation_only': False, 'inputs.correction': False,
        'inputs.external_schedule': False, 'inputs.recovery_check': 'off',
        'inputs.delivery_test_id': '', 'inputs.publication_mode': 'close_confirmed',
        'needs.regression.result': 'success', 'needs.check-trading-day.result': 'success',
        'needs.check-trading-day.outputs.SHOULD_RUN_MICROCAP': 'true',
        'steps.delivery_gate.outputs.should_send': 'true',
        'steps.delivery_gate.outputs.recover_marker': 'false',
        'steps.digest.outcome': 'success', 'steps.digest.outputs.status': 'OK',
        'steps.send_intent.outcome': 'success', 'steps.send_gmail.outcome': 'success',
        'steps.smtp_receipt.outcome': 'success',
        'steps.signals_v20.outputs.validated': 'true',
        'steps.signals_v23.outputs.validated': 'true',
        'steps.signals_v25.outputs.validated': 'true',
        'steps.whole_delivery.outputs.validated': 'true',
        'steps.publication_mode.outputs.mode': 'close_confirmed',
    }
    value.update(changes)
    return value


def sensitive(step):
    return ('send_report.py' in step.get('run', '')
            or 'delivery-marker/' in step.get('run', '')
            or 'marker_name' in str(step.get('with', {})))


@pytest.mark.parametrize('status', ['OK', 'FAILED', 'PARTIAL'])
def test_no_input_combination_can_reach_old_normal_or_error_mail_or_transactions(status):
    job = workflow()['jobs']['send']
    writes = [step for step in job['steps'] if sensitive(step)]
    assert {step['name'] for step in writes} == {
        'Recover delivery marker from accepted SMTP receipt', 'Mark recovered digest delivered',
        'Persist normal-send intent before SMTP', 'Send Gmail', 'Preserve accepted SMTP receipt',
        'Prepare delivery marker', 'Mark digest delivered',
    }
    # Even a fully successful upstream state and an explicit correction cannot
    # re-enable the retired sender, including its unsafe FAILED branch.
    for validation, correction, external, trading, mode, recovery, test_id in itertools.product(
        (False, True), (False, True), (False, True), ('false', 'true'),
        ('realtime', 'close_confirmed'), ('off', 'bootstrap', 'require_hit'), ('', 'retired-mail-test'),
    ):
        current = values(**{
            'inputs.validation_only': validation, 'inputs.correction': correction,
            'inputs.external_schedule': external, 'inputs.recovery_check': recovery,
            'inputs.delivery_test_id': test_id, 'inputs.publication_mode': mode,
            'needs.check-trading-day.outputs.SHOULD_RUN_MICROCAP': trading,
            'steps.digest.outputs.status': status, 'steps.publication_mode.outputs.mode': mode,
        })
        job_runs = condition(job['if'], current)
        reachable = [step['name'] for step in writes
                     if job_runs and condition(step['if'], current)]
        assert not job_runs and not reachable, (status, current, reachable)


@pytest.mark.parametrize('status', ['OK', 'FAILED', 'PARTIAL'])
def test_old_failed_and_normal_digest_cannot_enter_smtp_even_when_prior_gates_pass(status):
    job = workflow()['jobs']['send']
    send = next(step for step in job['steps'] if step.get('name') == 'Send Gmail')
    current = values(**{'steps.digest.outputs.status': status})
    # This coherent original C1/C2 case has no prior delivery marker/receipt,
    # no acceptance-test ID and a successful digest. A FAILED digest formerly
    # entered SMTP without its own persistent intent/receipt transaction.
    assert not (condition(job['if'], current) and condition(send['if'], current))


@pytest.mark.parametrize('mode', ['bootstrap', 'require_hit'])
@pytest.mark.parametrize('trading', ['false', 'true'])
def test_explicit_exact_archive_recovery_still_enters_restore_job(mode, trading):
    jobs = workflow()['jobs']
    current = values(**{'inputs.recovery_check': mode,
                        'needs.check-trading-day.outputs.SHOULD_RUN_MICROCAP': trading})
    assert condition(jobs['regression']['if'], current)
    assert condition(jobs['recovery-check']['if'], current)
    assert not condition(jobs['send']['if'], current)
    steps = {step.get('name'): step for step in jobs['recovery-check']['steps']}
    restore = steps['Restore close-confirmed whole delivery']['run']
    assert 'microcap_whole_state_recovery.py restore' in restore
    assert '--strategy-sha' in restore and '--not-after-date' in restore
    assert 'require_hit' in restore and '--require-hit' in restore
    assert 'Restore approved bootstrap only when no recovery artifact exists' in steps
    for job_name, job in jobs.items():
        if job_name == 'send':
            continue
        for step in job.get('steps', []):
            assert not sensitive(step)
            assert 'MAIL_PASSWORD' not in str(step.get('env', {}))


@pytest.mark.parametrize('validation,expected', [(False, True), (True, False)])
def test_new_member_daily_normal_send_permission_is_preserved(validation, expected):
    jobs = yaml.safe_load((ROOT / '.github/workflows/microcap-member-daily.yml').read_text(encoding='utf8'))['jobs']
    job = jobs['send']
    assert 'if' not in job
    step = next(step for step in job['steps'] if step.get('name') == 'Send Gmail')
    assert condition(step['if'], {'inputs.validation_only': validation, 'steps.intent.outcome': 'success'}) is expected
    assert next(step for step in job['steps'] if step.get('name') == 'Check delivery marker')['run'].count('check_microcap_delivery.py') == 1
