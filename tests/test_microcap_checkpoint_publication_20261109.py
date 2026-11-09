"""No-send state acceptance cannot become an alternate mail transaction."""
import os
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]


def workflow():
    return yaml.load((ROOT / '.github/workflows/microcap-member-daily.yml').read_text(encoding='utf8'), Loader=yaml.BaseLoader)


def validate(monkeypatch, **changes):
    values = dict(MODE='close_confirmed', REPORT_URL='', REPORT_SHA='',
                  EXTERNAL='false', VALIDATE='true', PERSIST_CHECKPOINT='true')
    values.update(changes)
    for key, value in values.items(): monkeypatch.setenv(key, value)
    step = next(s for s in workflow()['jobs']['send']['steps'] if s.get('name') == 'Validate publication clock and recovery inputs')
    code = step['run'].split("python - <<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
    exec(compile(code, '<actual-workflow-input-validation>', 'exec'), {'os': os})


def test_explicit_manual_close_no_send_checkpoint_acceptance_is_allowed(monkeypatch):
    validate(monkeypatch)


@pytest.mark.parametrize('changes', [dict(VALIDATE='false'), dict(EXTERNAL='true'),
    dict(MODE='realtime'), dict(REPORT_URL='saved-report', REPORT_SHA='a'*64)])
def test_checkpoint_acceptance_cannot_send_use_external_schedule_or_replace_inputs(monkeypatch, changes):
    with pytest.raises(ValueError, match='explicit manual no-send close-only'):
        validate(monkeypatch, **changes)


def test_normal_schedule_defaults_and_all_send_boundaries_are_preserved():
    flow = workflow(); inputs = flow['on']['workflow_dispatch']['inputs']
    assert inputs['persist_checkpoint']['default'] == 'false'
    steps = {s.get('name'): s for s in flow['jobs']['send']['steps'] if s.get('name')}
    for name in ('Persist normal-send intent before SMTP', 'Send Gmail', 'Preserve accepted SMTP receipt', 'Mark digest delivered'):
        assert 'inputs.validation_only != true' in steps[name]['if']
        assert 'persist_checkpoint' not in steps[name]['if']
    assert 'inputs.persist_checkpoint == true' in steps['Publish verified member checkpoint']['if']


def test_ci_checks_the_actual_member_runtime_without_becoming_a_daily_dependency():
    ci = yaml.load((ROOT / '.github/workflows/delivery-regression.yml').read_text(encoding='utf8'), Loader=yaml.BaseLoader)
    steps = ci['jobs']['member-daily-tests']['steps']
    assert any('config/microcap_member_runtime.json' in s.get('run', '') for s in steps)
    assert any(s.get('with', {}).get('ref') == '${{ steps.member.outputs.sha }}' for s in steps)
    assert any('test_member_daily_transport_20261109.py' in s.get('run', '') for s in steps)
    assert 'needs' not in workflow()['jobs']['send']
