"""Optional-reference errors must be visible without suppressing valid old input."""
import copy
import gzip
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import build_ic_im_v1_4_digest as digest

PAYLOAD = json.loads(gzip.decompress((ROOT/'tests/fixtures/icim/fear_split_20261109/result.json.gz').read_bytes()))


def test_structured_reference_survives_absent_or_stale_warning():
    for warning in ('', '新增综合恐贪99.99，旧说明不是来源真值'):
        value = copy.deepcopy(PAYLOAD)
        for signal in value['signals'].values(): signal['fear_source_warning'] = warning
        _, body, _ = digest.build_success(value, '', '')
        html = digest.build_success_html(value, '')
        assert '10.010' in body and '10.010' in html
        assert '99.99' not in body and '99.99' not in html


@pytest.mark.parametrize('fault', ['role', 'future_date', 'value_snapshot', 'mixed_legs', 'nonfinite', 'missing'])
def test_bad_optional_reference_degrades_only_new_display(fault):
    value = copy.deepcopy(PAYLOAD)
    ref = value['signals']['IM']['fear_composite_reference']
    if fault == 'role': ref['role'] = 'strategy_input'
    elif fault == 'future_date': ref['data_date'] = '2026-11-10'
    elif fault == 'value_snapshot': ref['value'] = 90
    elif fault == 'mixed_legs': ref.update(value=90, source_values={'csv':90, 'page':90})
    elif fault == 'nonfinite': ref['value'] = float('nan')
    else: value['signals']['IM'].pop('fear_composite_reference')
    note = digest.composite_reference_text(value)
    assert '新增综合恐贪：N/A' in note and '仅观察' in note
    _, body, _ = digest.build_success(value, '', '')
    html = digest.build_success_html(value, '')
    assert '18.970' in body and '18.970' in html
    assert '新增综合恐贪：N/A' in body and '新增综合恐贪：N/A' in html


def test_wrong_old_strategy_identity_still_blocks_normal_report():
    value = copy.deepcopy(PAYLOAD)
    for signal in value['signals'].values(): signal['fear_strategy_metric'] = 'new_composite'
    with pytest.raises(ValueError): digest.build_success(value, '', '')


@pytest.mark.parametrize('legs', [('IM',), ('IC', 'IM')])
def test_missing_references_never_restore_free_text_metric_values(legs):
    value = copy.deepcopy(PAYLOAD)
    for signal in value['signals'].values(): signal['fear_source_warning'] = '新增综合恐贪99.99'
    for leg in legs: value['signals'][leg].pop('fear_composite_reference')
    _, body, _ = digest.build_success(value, '', '')
    html = digest.build_success_html(value, '')
    assert '99.99' not in body and '99.99' not in html
    assert '新增综合恐贪：N/A' in body and '新增综合恐贪：N/A' in html
