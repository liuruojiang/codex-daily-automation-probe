"""A late optional observation must not inherit an earlier report-completion clock."""
import copy
from datetime import datetime, timedelta, timezone
import gzip
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build_ic_im_v1_4_digest as digest

PAYLOAD = json.loads(gzip.decompress((ROOT / 'tests/fixtures/icim/fear_split_20261109/result.json.gz').read_bytes()))


@pytest.mark.parametrize('status', ['ok', 'failed'])
def test_post_report_optional_observation_is_na_without_hiding_old_input(status):
    value = copy.deepcopy(PAYLOAD)
    value['status'] = status
    complete = datetime.fromisoformat(value['report_generated_at'])
    late = (complete + timedelta(seconds=1)).isoformat()
    for signal in value['signals'].values():
        signal['fear_composite_reference']['retrieved_at'] = late
    if status == 'failed':
        observation = value['signals']['IC']
        value.update({key: item for key, item in observation.items() if key.startswith(('fear_', 'jiu_'))})
        value.pop('signals')
        _, body = digest.build_failure(value, '', '')
        html = digest.build_failure_html(value, '')
    else:
        _, body, _ = digest.build_success(value, '', '')
        html = digest.build_success_html(value, '')
    for output in (body, html):
        assert '18.970' in output
        assert '新增综合恐贪：N/A' in output
        assert '报告完成' in output
        assert late not in output


def test_equal_report_clock_in_other_timezone_is_accepted():
    value = copy.deepcopy(PAYLOAD)
    complete = datetime.fromisoformat(value['report_generated_at'])
    for signal in value['signals'].values():
        signal['fear_composite_reference']['retrieved_at'] = complete.astimezone(timezone.utc).isoformat()
    assert '10.010' in digest.composite_reference_text(value)
    assert 'N/A' not in digest.composite_reference_text(value)


def test_failed_observation_without_completion_keeps_explicit_collection_clock():
    value = copy.deepcopy(PAYLOAD)
    value['status'] = 'failed'
    observation = value['signals']['IC']
    value.update({key: item for key, item in observation.items() if key.startswith(('fear_', 'jiu_'))})
    value.pop('signals')
    value.pop('report_generated_at')
    # generated_at records the query start, not completion; do not turn it into a false bound.
    value['generated_at'] = '2026-11-09T14:00:00+08:00'
    _, body = digest.build_failure(value, '', '')
    assert '10.010' in body and 'N/A' not in digest.composite_reference_text(value)


@pytest.mark.parametrize('seconds_later', [0, 1])
def test_sparse_success_archive_uses_displayed_generated_completion(seconds_later):
    value = copy.deepcopy(PAYLOAD)
    complete = datetime.fromisoformat(value.pop('report_generated_at'))
    value['generated_at'] = complete.isoformat()
    for signal in value['signals'].values():
        signal['fear_composite_reference']['retrieved_at'] = (complete + timedelta(seconds=seconds_later)).isoformat()
    text = digest.composite_reference_text(value)
    assert ('N/A' in text) is bool(seconds_later)


def test_failed_missing_completion_and_start_does_not_invent_an_observation_cap():
    value = copy.deepcopy(PAYLOAD)
    value['status'] = 'failed'
    value.pop('report_generated_at')
    value.pop('generated_at')
    assert '10.010' in digest.composite_reference_text(value)


@pytest.mark.parametrize('stamp', ['not-a-time', '2026-11-09T16:00:00'])
def test_unverified_optional_completion_clock_degrades_only_new_reference(stamp):
    value = copy.deepcopy(PAYLOAD)
    value['report_generated_at'] = stamp
    assert '新增综合恐贪：N/A' in digest.composite_reference_text(value)
