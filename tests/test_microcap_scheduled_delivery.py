from datetime import date
import json
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import check_microcap_scheduled_delivery as scheduled


DAY = date(2026, 11, 26)
SLOT = 'microcap-dispatch:microcap-daily-20261126T080000772Z'
RUN = 45779506447


@pytest.fixture
def evidence():
    start = scheduled.delivery_marker_name(DAY, 'close_confirmed')
    rows = [
        dict(path='.github/workflows/microcap-member-daily.yml', event='workflow_dispatch',
             head_branch='main', display_title=SLOT, id=RUN, status='completed',
             conclusion='success', head_sha='a'*40, created_at='2026-11-26T08:00:03Z'),
        dict(path='.github/workflows/microcap-member-daily.yml', event='workflow_dispatch',
             head_branch='main', display_title='Microcap Member Daily', id=RUN+1, status='completed',
             conclusion='success', head_sha='b'*40, created_at='2026-11-26T18:30:00Z'),
    ]
    objects = {}
    verified = []

    def put(name, id, run=RUN):
        if name.startswith('-'):
            name = start + name
        elif name == '':
            name = start
        objects.setdefault(name, []).append(
            dict(id=id, name=name, expired=False, workflow_run=dict(id=run)))

    def api(method, path):
        assert method == 'GET'
        query = parse_qs(urlsplit(path).query)
        if urlsplit(path).path.endswith('/runs'):
            if '/microcap-member-maintenance.yml/' in path:
                assert query['created'] == ['2026-11-26T07:09:00Z..2026-11-27T07:10:00Z']
                original = dict(rows[0], path='.github/workflows/microcap-member-maintenance.yml',
                    display_title='microcap-dispatch:microcap-rebalance-20261126T071044828Z',
                    created_at='2026-11-26T07:10:48Z', id=45773398913, conclusion='failure')
                return dict(total_count=1, workflow_runs=[original])
            assert query['created'] == ['2026-11-26T07:59:00Z..2026-11-27T08:00:00Z']
            return dict(total_count=len(rows), workflow_runs=rows)
        name = query['name'][0]
        found = objects.get(name, [])
        return dict(total_count=len(found), artifacts=found)

    def verify(repo, item, day):
        assert repo == scheduled.REPO and day == DAY
        verified.append(item['id'])
        return dict(body_sha256='1'*64, metadata_sha256='2'*64, receipt_archive_sha256='3'*64)

    def check(**kwargs):
        return scheduled.check(DAY, api=api, verify_receipt=verify, **kwargs)

    def recover(phase, id):
        return scheduled.marker_recovery(DAY, phase, id, api=api, verify_receipt=verify)

    return rows, put, check, recover, verified


def test_green_no_send_run_without_original_scheduled_slot_is_a_gap(evidence):
    rows, _, check, _, _ = evidence
    rows.pop(0)
    value = check()
    assert value['delivery'] == 'no_confirmed_delivery'
    assert value['slot']['status'] == 'missing'
    assert {action['action'] for action in value['follow_up']} == {
        'diagnose_original_scheduled_slot', 'check_original_dated_report_before_manual_recovery'}


def test_confirmed_normal_delivery_is_attributed_to_original_slot(evidence):
    _, put, check, _, verified = evidence
    put('', 1)
    put('-smtp-accepted', 2)
    value = check()
    assert value['delivery'] == 'full_delivered'
    assert value['slot']['confirmed_delivery_from_slot']
    assert value['follow_up'] == []
    assert verified == [2]


def test_partial_already_sent_has_no_mail_retry_and_handoff_is_independent(evidence, monkeypatch):
    _, put, check, _, _ = evidence
    put('-partial-information', 1)
    put('-partial-information-smtp-accepted', 2)
    monkeypatch.setattr(scheduled, '_handoff', lambda *_: dict(
        status='rebalance_handoff_pending', owner='existing_09_00_11_00_recovery'))
    value = check()
    assert value['delivery'] == 'partial_delivered'
    assert value['follow_up'] == [dict(owner='existing_09_00_11_00_recovery',
                                      action='resume_original_no_send_native_recovery')]


def test_unconfirmed_intent_requires_original_reconciliation_never_send(evidence):
    _, put, check, recover, _ = evidence
    put('-partial-information-send-intent', 8)
    value = check()
    assert value['follow_up'] == [dict(owner='microcap_16_00_delivery', action='reconcile_original_smtp_and_inbox')]
    with pytest.raises(ValueError, match='no unique, exact verified'):
        recover('partial', 8)


@pytest.mark.parametrize('phase', ['normal', 'partial'])
def test_original_verified_receipt_repairs_only_its_marker_and_is_idempotent(evidence, phase):
    _, put, check, recover, _ = evidence
    suffix = '' if phase == 'normal' else '-partial-information'
    put(suffix + '-smtp-accepted', 4)
    assert check()['follow_up'][0]['receipt_id'] == 4
    recovered = recover(phase, 4)
    assert recovered['write_marker']
    assert recovered['original_scheduled_run_id'] == RUN
    assert recovered['smtp_receipt']['body_sha256'] == '1'*64
    put(suffix, 5)
    assert not recover(phase, 4)['write_marker']
    assert not check()['follow_up']
    with pytest.raises(ValueError, match='no unique, exact verified'):
        recover(phase, 99)


def test_receipt_from_different_run_cannot_restore_original_slot_marker(evidence):
    _, put, check, recover, _ = evidence
    put('-smtp-accepted', 4, RUN+1)
    assert check()['follow_up'][0]['action'] == 'reconcile_original_smtp_and_inbox'
    with pytest.raises(ValueError, match='no unique, exact verified'):
        recover('normal', 4)


def test_local_consumer_must_prove_same_checkpoint_bytes_after_rebalance(evidence, tmp_path, monkeypatch):
    def write(name, value):
        path = tmp_path / name
        path.write_bytes(scheduled.encode(value))
        return path

    period = write('period.json', dict(anchor_day=DAY.isoformat(), effective_from='2026-11-27'))
    period_sha = scheduled.sha(period.read_bytes())
    checkpoint = write('checkpoint.json', dict(as_of_date=DAY.isoformat(), period_status='current',
                                               period=dict(path='period.json', sha256=period_sha)))
    checkpoint_sha = scheduled.sha(checkpoint.read_bytes())
    plan = write('plan.json', dict(schema=scheduled.PLAN_SCHEMA, target=DAY.isoformat(),
        latest_completed_session=DAY.isoformat(), sessions=[DAY.isoformat(),'2026-11-27'],
        rebalance_dates=[DAY.isoformat()], latest_checkpoint_path=str(checkpoint),
        latest_checkpoint_sha256=checkpoint_sha, latest_checkpoint_period_sha256=period_sha,
        latest_checkpoint_period_anchor=DAY.isoformat(), latest_checkpoint_as_of_date=DAY.isoformat(),
        latest_checkpoint_period_status='current'))
    monkeypatch.setattr(scheduled, 'validate_checkpoint_directory', lambda *_: None)
    _, put, check, _, _ = evidence
    put('', 1)
    put('-smtp-accepted', 2)
    assert check(plan=plan)['handoff']['status'] == 'current_handoff_ready_for_local_consumer'
    assert check(plan=plan, require_local=True)['handoff']['status'] == 'local_consumer_has_not_read_current_handoff'
    receipt = dict(schema='microcap_member_local_checkpoint_readback_v1', as_of_date=DAY.isoformat(),
                   mode='prepare', date='2026-11-27', native_verify_exit_code=0, checkpoint=str(checkpoint),
                   checkpoint_sha256=checkpoint_sha)
    prepare = write('checkpoint_readback.json', receipt)
    install = write('install_readback.json', dict(checkpoint_receipt=dict(
        path=str(prepare), sha256=scheduled.sha(prepare.read_bytes()), readback=receipt)))
    verified = check(plan=plan, local_preparation=install)['handoff']
    assert verified['status'] == 'verified_next_local_consumer'
    assert verified['original_rebalance_slot']['runs'][0]['conclusion'] == 'failure'
    prepare.write_text(json.dumps(dict(receipt, checkpoint_sha256='6'*64)))
    result = check(plan=plan, local_preparation=install)
    assert result['delivery'] == 'full_delivered'
    assert result['handoff']['status'] == 'native_handoff_evidence_unconfirmed'


def test_workflow_marker_job_is_disjoint_from_the_smtp_job():
    root = Path(__file__).resolve().parents[1]
    flow = (root/'.github/workflows/microcap-member-daily.yml').read_text(encoding='utf8')
    marker, normal = flow.split('  send:\n', maxsplit=1)
    assert '--marker-only "$PHASE" --receipt-id "$RECEIPT"' in marker
    assert 'send_report.py' not in marker and 'secrets.MAIL_' not in marker
    assert 'steps.original.outputs.write_marker' in marker
    assert "if: inputs.reconcile_delivery_date == '' && inputs.reconcile_delivery_phase == '' && inputs.reconcile_receipt_id == ''" in normal
