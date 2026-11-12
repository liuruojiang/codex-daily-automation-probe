"""Evaluate actual no-send recovery graph, including failed-stage preservation."""
from pathlib import Path
import sys

import pytest
import yaml

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
from test_microcap_whole_hit_normal_chain import condition


def flow():
    return yaml.safe_load((ROOT/'.github/workflows/microcap-member-maintenance.yml').read_text(encoding='utf-8'))


def state(task='rebalance_recovery',run='true'):
    return {'inputs.task':task,'steps.config.outputs.run':'true','steps.schedule.outputs.run':'true',
            'steps.plan.outputs.run':run,'steps.sealed.outputs.ready':'',
            'steps.checkpoint_publication.outputs.should_publish':run,
            'steps.whole_publication.outputs.should_publish':run,
            'steps.orphan.outputs.should_publish':'false'}


def enters(step, values, *, success=True, failed=False):
    expression=step.get('if','true')
    expression=expression.replace('success()','true' if success else 'false')
    expression=expression.replace('failure()','true' if failed else 'false').replace('cancelled()','false')
    return condition(expression,values)


def steps():
    return flow()['jobs']['scoped']['steps']


def named(name):
    return next(item for item in steps() if item.get('name')==name)


HEAVY=[
    'Restore verified scoped whole delivery','Restore original verified weekly maintenance roster',
    'Capture original R boundaries before source collection','Prepare current close sources for exactly 300 candidates',
    'Restore previous daily member checkpoint','Earn original formal rebalance and next-session handoff',
    'Publish verified member checkpoint','Pack verified scoped whole state','Publish verified scoped whole state',
]


def test_explicit_recovery_is_allowed_without_changing_legacy_defaults():
    root=flow(); trigger=root.get('on',root.get(True))
    task=trigger['workflow_dispatch']['inputs']['task']
    assert task['default']=='maintenance' and 'rebalance_recovery' in task['options']
    assert root['concurrency']=={'group':'microcap-independent-maintenance','cancel-in-progress':False}


@pytest.mark.parametrize('task',['rebalance','rebalance_recovery'])
def test_same_D_current_noop_skips_every_expensive_or_publishing_step(task):
    values=state(task,run='false')
    assert enters(named('Restore latest member checkpoint for recovery planning'),values)
    assert enters(named('Plan original missed periods against the real completed session'),values)
    for name in HEAVY:
        assert not enters(named(name),values),name
    source=next(item for item in steps() if item.get('with',{}).get('path')=='scoped-source-state/runtime')
    assert not enters(source,values)


@pytest.mark.parametrize('task',['rebalance','rebalance_recovery'])
def test_missed_R_on_non_R_enters_same_no_send_formal_chain(task):
    values=state(task)
    for name in HEAVY:
        assert enters(named(name),values),name
    schedule=named('Resolve scoped close and original rebalance schedule')['run']
    assert 'latest_completed_session()' in schedule
    assert "or value['is_rebalance']" not in schedule
    planner=named('Plan original missed periods against the real completed session')['run']
    assert '--checkpoint scoped-latest/checkpoint.json' in planner and '--task "$TASK"' in planner


def test_source_collection_cannot_precede_preserved_original_R_boundaries():
    names=[item.get('name') for item in steps()]
    assert names.index('Capture original R boundaries before source collection') < names.index('Seal original R boundary recovery evidence')
    assert names.index('Seal original R boundary recovery evidence') < names.index('Preserve original R boundary recovery evidence')
    assert names.index('Preserve original R boundary recovery evidence') < names.index('Prepare current close sources for exactly 300 candidates')
    early=named('Preserve original R boundary recovery evidence')['with']['path']
    assert 'scoped-prepare/boundaries' in early and 'scoped-boundary-manifest.json' in early
    assert 'scoped-prepare/prepared.json' not in early


def test_original_parent_is_found_before_first_R_not_latest_awaiting_D():
    parent=named('Restore previous daily member checkpoint')
    assert parent['env']=={'PARENT_CEILING':'${{ steps.plan.outputs.parent_not_after_date }}'}
    assert '--not-after-date "$PARENT_CEILING"' in parent['run']
    assert '--not-after-date "$TARGET"' not in parent['run']


@pytest.mark.parametrize('name',['Capture original R boundaries before source collection',
                                'Prepare current close sources for exactly 300 candidates',
                                'Earn original formal rebalance and next-session handoff'])
def test_original_recovery_plan_and_closed_manifest_reach_each_native_stage(name):
    command=named(name)['run']
    assert '--recovery-plan scoped-recovery-plan.json' in command
    assert '--resume-state' in command and 'steps.resume.outputs.manifest' in command


def test_successful_formal_archive_is_copied_not_repacked_after_handoff():
    command=named('Pack verified scoped whole state')['run']
    assert 'test -f scoped-delivery/formal-state.zip' in command
    assert 'cp scoped-delivery/formal-state.zip' in command
    assert 'top100_cloud_delivery.py pack' not in command


def test_failure_still_seals_partial_state_but_cannot_publish_it_as_formal():
    values=state()
    assert enters(named('Seal resumable scoped recovery state'),values,success=False,failed=True)
    assert not enters(named('Publish verified member checkpoint'),values,success=False,failed=True)
    assert not enters(named('Publish verified scoped whole state'),values,success=False,failed=True)
    values['steps.sealed.outputs.ready']='true'
    assert enters(named('Preserve resumable scoped recovery state'),values,success=False,failed=True)
    assert not enters(named('Preserve failed scoped producer sources'),values,success=False,failed=True)


def test_seal_failure_keeps_legacy_failed_sources_as_independent_fallback():
    values=state()
    assert enters(named('Preserve failed scoped producer sources'),values,success=False,failed=True)
    assert not enters(named('Preserve resumable scoped recovery state'),values,success=False,failed=True)


@pytest.mark.parametrize('task',['scoped_acceptance','scoped_readback'])
def test_existing_archive_only_acceptance_does_not_start_producer_or_recovery_plan(task):
    values=state(task,run='')
    assert enters(named('Restore verified scoped whole delivery'),values)
    assert enters(named('Verify complete restored three-version archive without sending'),values)
    assert not enters(named('Plan original missed periods against the real completed session'),values)
    assert not enters(named('Prepare current close sources for exactly 300 candidates'),values)


def test_no_task_in_maintenance_workflow_can_reach_smtp_or_daily_send_transactions():
    root=flow()
    all_steps=[item for job in root['jobs'].values() for item in job.get('steps',[])]
    for item in all_steps:
        script=item.get('run','').lower()
        assert not any(token in script for token in ('send_report.py','smtplib.','smtp_receipt','delivery-marker/','send_intent.py'))
    assert not any('overwrite' in item.get('with',{}) for item in all_steps if 'upload-artifact' in item.get('uses',''))


def test_known_accepted_checkpoint_or_whole_object_skips_second_upload():
    values=state()
    values['steps.checkpoint_publication.outputs.should_publish']='false'
    values['steps.whole_publication.outputs.should_publish']='false'
    assert not enters(named('Publish verified member checkpoint'),values)
    assert not enters(named('Publish verified scoped whole state'),values)


def test_prepublication_intent_is_preserved_before_either_writer():
    names=[item.get('name') for item in steps()]
    assert names.index('Reconcile exact checkpoint publication before writing') < names.index('Preserve checkpoint publication intent before upload') < names.index('Publish verified member checkpoint')
    assert names.index('Reconcile exact whole archive publication before writing') < names.index('Preserve whole publication intent before upload') < names.index('Publish verified scoped whole state')


def test_checkpoint_noop_still_checks_orphan_and_can_publish_only_validated_original_zip():
    values=state(run='false')
    assert enters(named('Restore sealed partial scoped recovery evidence'),values)
    assert '--orphan-only' in named('Restore sealed partial scoped recovery evidence')['run']
    values.update({'steps.resume.outputs.restored':'true','steps.resume.outputs.formal_archive':'original.zip'})
    assert enters(named('Validate preserved earned whole archive without refreshing'),values)
    values['steps.orphan.outputs.should_publish']='true'
    assert enters(named('Pack verified scoped whole state'),values)
    assert enters(named('Publish verified scoped whole state'),values)
    for name in ('Restore verified scoped whole delivery','Prepare current close sources for exactly 300 candidates',
                 'Earn original formal rebalance and next-session handoff','Publish verified member checkpoint'):
        assert not enters(named(name),values)
