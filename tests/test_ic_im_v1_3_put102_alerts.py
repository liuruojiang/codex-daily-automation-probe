import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import build_ic_im_v1_3_digest as digest


def test_strong_warning_visible_in_html_and_text_without_action():
    signal={'iv_warning': {'level':'critical','text':'IV超过50%：强预警；IV 51.00%；仅预警，不改变仓位。'}}
    assert 'IV超过50%' in digest.iv_warning_text(signal)
    assert '#b42318' in digest.iv_warning_html(signal)
    assert digest.action_parts(signal)==[]


def test_new_momentum_rule_replaces_old_formula():
    signal={'im_put_policy_description':'动量Put仅MOM120；新选约102%'}
    text=digest.put_reason('IM',signal)
    assert '动量Put仅MOM120' in text and '102%' in text
    assert '动量Put按父规则数量' not in text
