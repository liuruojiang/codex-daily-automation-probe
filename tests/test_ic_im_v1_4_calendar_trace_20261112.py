"""Use the retained real failed-render result, never a fresh producer run."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build_ic_im_v1_4_digest as digest
import ic_im_v1_4_delivery_contract as contract

FIXTURE = ROOT / 'tests/fixtures/icim/calendar_render_20261112'


def payload():
    return json.loads((FIXTURE / 'result.json').read_bytes())


@contract.with_calendar_policy(verify_source_metadata=True)
def consume(value):
    return True


def test_actual_retained_trace_accepts_all_four_uses_and_holiday_month_marker():
    value = payload()
    before = contract._CALENDAR_POLICY_DAY.get()
    assert consume(value)
    assert contract._CALENDAR_POLICY_DAY.get() is before
    rows = value['signals']['IM']['calendar_source_evidence']['sources']
    assert len(rows) == 4
    assert {row['date'] for row in rows} == {'2027-01-01', '2027-01-15'}


@pytest.mark.parametrize('fault', ['missing_evidence', 'missing_row', 'duplicate_row', 'bare_source',
 'wrong_date', 'wrong_purpose', 'unknown_date', 'source_hash', 'extra_field', 'missing_date',
 'numeric_accepted', 'numeric_confirmed', 'numeric_year', 'top_numeric_confirmed', 'before_adoption',
 'changed_consumed_contract', 'unknown_consumed_contract'])
def test_real_trace_cannot_lose_consumption_or_relabel_its_authority(fault):
    value = payload()
    signal = value['signals']['IM']
    evidence = signal['calendar_source_evidence']
    rows = evidence['sources']
    if fault == 'missing_evidence': signal.pop('calendar_source_evidence')
    elif fault == 'missing_row': rows.pop()
    elif fault == 'duplicate_row': rows.append(deepcopy(rows[0]))
    elif fault == 'bare_source':
        for row in rows:
            row.pop('date'); row.pop('purpose')
    elif fault == 'wrong_date': rows[0]['date'] = '2027-01-02'
    elif fault == 'wrong_purpose': rows[0]['purpose'] = 'core target'
    elif fault == 'unknown_date': rows[0]['date'] = '2028-01-01'
    elif fault == 'source_hash': rows[0]['source_text_sha256'] = '0' * 64
    elif fault == 'extra_field': rows[0]['unreviewed'] = True
    elif fault == 'missing_date': rows[0].pop('date')
    elif fault == 'numeric_accepted': rows[0]['accepted_for_research'] = 1
    elif fault == 'numeric_confirmed': rows[0]['exchange_notice_verified'] = 0
    elif fault == 'numeric_year': rows[0]['year'] = 2027.0
    elif fault == 'top_numeric_confirmed': evidence['exchange_notice_verified'] = 0
    elif fault == 'before_adoption': value['market_date'] = '2026-11-11'
    elif fault == 'changed_consumed_contract': signal['momentum_put_target_contract'] = 'MO2702-P-6600'
    else: signal['momentum_put_target_contract'] = 'MO2801-P-6600'
    before = contract._CALENDAR_POLICY_DAY.get()
    with pytest.raises(ValueError): consume(value)
    assert contract._CALENDAR_POLICY_DAY.get() is before


def test_retained_result_and_report_render_with_exact_original_hashes(tmp_path, monkeypatch):
    proof = json.loads((FIXTURE / 'provenance.json').read_bytes())
    for name, expected in proof['files'].items():
        assert hashlib.sha256((FIXTURE / name).read_bytes()).hexdigest() == expected
    value = payload()
    assert value['status'] == 'ok' and value['verified_day'] == '2026-11-12' and value['sequence'] == 35
    monkeypatch.setattr(sys, 'argv', ['build_ic_im_v1_4_digest.py', '--result', str(FIXTURE / 'result.json'),
       '--report', str(FIXTURE / 'ic_im_v1_4_close_signal.md'), '--source-provenance', str(FIXTURE / 'program-source.json'),
       '--expected-source-sha', '9ec8b91c5256155101faae3c84dba9ff95632db5',
       '--expected-source-release', '20261112-expert-review-v5-r3', '--out-dir', str(tmp_path / 'rendered')])
    assert digest.main() == 0
    metadata = json.loads((tmp_path / 'rendered/metadata.json').read_bytes())
    assert '2026-11-12' in metadata['subject']
    assert metadata['runtime_source_provenance']['strategy_sha'] == proof['strategy_sha']
    assert (tmp_path / 'rendered/ic_im_v1_4_digest.html').stat().st_size > 1000
