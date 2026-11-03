from pathlib import Path
import yaml
import pytest
from test_microcap_refresh_permanent_failure import execute_shell

ROOT=Path(__file__).resolve().parents[1]

def steps():
    return yaml.safe_load((ROOT/'.github/workflows/microcap-realtime-digest.yml').read_text(encoding='utf-8'))['jobs']['send']['steps']

@pytest.mark.parametrize('mode,count',[('close_confirmed',0),('realtime',3)])
def test_actual_preparation_shell_avoids_intermediate_close_bundle(tmp_path,mode,count):
    step=next(x for x in steps() if x.get('id')=='state_bundle')
    assert "steps.refresh_state.outputs.validated == 'true'" in step['if']
    script=step['run'].replace('${{ steps.publication_mode.outputs.mode }}',mode)
    execute_shell('RUNNER_TEMP=.\nEXPECTED_DATE=2026-11-02\npython() { printf "%s\\n" "$*" >> calls; }\n'+script,tmp_path)
    calls=(tmp_path/'calls').read_text().splitlines() if (tmp_path/'calls').exists() else []
    assert len(calls)==count
    if count:
        assert 'validate' in calls[0] and 'check_microcap_refresh_target.py' in calls[1] and 'pack' in calls[2]

def test_one_checkout_shared_sequential_results_and_all_three_gate():
    sequence=steps();named={x.get('name'):x for x in sequence}
    assert len([x for x in sequence if x.get('with',{}).get('repository')=='liuruojiang/microcap'])==1
    order=[]
    for v in ('0','3','5'):
        step=named[f'Run v2.{v} selected signal']
        assert step['working-directory']=='microcap'
        assert "steps.state_bundle.outcome == 'success'" in step['if']
        order.append(sequence.index(step))
    packed=named['Verify and pack all three final deliveries']
    assert order==sorted(order) and order[-1]<sequence.index(packed)
    assert '--root microcap --v23-root microcap --v25-root microcap' in packed['run']
    for v in ('20','23','25'):
        assert f"steps.signals_v{v}.outputs.validated == 'true'" in packed['if']
    assert "steps.whole_delivery.outputs.validated == 'true'" in named['Send Gmail']['if']
