"""Offline workflow counterexamples for the member-daily split only."""
from pathlib import Path
import json
import os
import subprocess
import sys
import yaml
import pytest

ROOT = Path(__file__).resolve().parents[1]


def steps():
    return yaml.safe_load((ROOT / '.github/workflows/microcap-member-daily.yml').read_text(encoding='utf8'))['jobs']['send']['steps']


@pytest.mark.parametrize('failure', ['Verify close checkpoint candidate', 'Publish verified member checkpoint'])
def test_checkpoint_only_failure_does_not_poison_valid_report_delivery(failure):
    """Actions applies implicit success() unless failure is explicitly isolated."""
    flow = steps()
    failed = next(s for s in flow if s.get('name') == failure)
    build = next(s for s in flow if s.get('name') == 'Build member digest')
    assert failed.get('continue-on-error') is True, failure
    assert 'always()' in build['if']
    assert "steps.compute.outcome == 'success'" in build['if']
    assert "steps.saved.outcome == 'success'" in build['if']
    assert "steps.reviewed.outcome == 'success'" in build['if']
    # Bad/missing computations do not authorize mail merely through always().
    assert 'steps.gate.outputs.should_send' in build['if']


def test_checkpoint_failure_is_disclosed_without_editing_original_result():
    build = next(s for s in steps() if s.get('name') == 'Build member digest')
    assert 'checkpoint_persistence' in build['run']
    assert 'delivery_result.json' in build['run']
    assert "Path('daily-result/result.json').write" not in build['run']


@pytest.mark.parametrize('verification,upload', [('failure','skipped'),('success','failure')])
def test_actual_workflow_warning_script_retains_signal_and_source_result(tmp_path, verification, upload):
    build=next(s for s in steps() if s.get('name')=='Build member digest')
    script=build['run'].split("python - <<'PY'\n",1)[1].split('\nPY\n',1)[0]
    (tmp_path/'daily-result').mkdir();(tmp_path/'artifacts').mkdir()
    original={'status':'OK','versions':[{'version':'v2.5','signal':{'next_holding':'cash'}}],'warnings':[]}
    raw=json.dumps(original).encode();(tmp_path/'daily-result/result.json').write_bytes(raw)
    env={**os.environ,'CHECKPOINT_VERIFY_OUTCOME':verification,'CHECKPOINT_UPLOAD_OUTCOME':upload}
    subprocess.run([sys.executable,'-B','-c',script],cwd=tmp_path,env=env,check=True,capture_output=True)
    rendered=json.loads((tmp_path/'artifacts/delivery_result.json').read_bytes())
    assert (tmp_path/'daily-result/result.json').read_bytes()==raw
    assert rendered['versions']==original['versions'] and rendered['status']=='OK'
    assert len(rendered['warnings'])==1 and '尚未持久化' in rendered['warnings'][0]
    assert rendered['checkpoint_persistence']=={'verification':verification,'upload':upload}


def test_maintenance_cannot_queue_behind_daily_concurrency_or_be_a_daily_need():
    daily=yaml.safe_load((ROOT/'.github/workflows/microcap-member-daily.yml').read_text(encoding='utf8'))
    maintenance=yaml.safe_load((ROOT/'.github/workflows/microcap-member-maintenance.yml').read_text(encoding='utf8'))
    assert daily['concurrency']['group'] != maintenance['concurrency']['group']
    assert 'needs' not in daily['jobs']['send']
    runs='\n'.join(s.get('run','') for s in steps())
    for forbidden in ('refresh-all','refresh_microcap_notice_retry','microcap_whole_state_recovery','realtime_daily_gate'):
        assert forbidden not in runs
