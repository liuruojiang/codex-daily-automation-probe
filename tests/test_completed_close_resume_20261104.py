import copy
import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('completed_resume', ROOT / 'scripts/resume_completed_microcap_close_20261104.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
EVIDENCE = Path(os.environ['COMPLETED_CLOSE_EVIDENCE'])
PRIOR = json.loads((EVIDENCE / 'notice_boundary_41865471442/prior_native_rows.json').read_bytes())
CURRENT = {k: v for k, v in PRIOR.items() if k != m.REMOVED}


def review(old, new, classify=lambda title: {}, select=lambda row, symbol: None):
    def strict(a, b):
        if a != b:
            raise ValueError('native stable content guard')
        return {'tested_common_rows': len(a)}
    return m.exact_notice_review(old, new, classify, select, strict)


def test_exact_actual_source_removal_only():
    assert review(PRIOR, CURRENT)['economic_results_changed'] is False


@pytest.mark.parametrize('change', ['addition', 'other_removal', 'title', 'category', 'common_change', 'no_removal'])
def test_unreviewed_changes_fail(change):
    old, new = copy.deepcopy(PRIOR), copy.deepcopy(CURRENT)
    if change == 'addition': new['new'] = copy.deepcopy(next(iter(new.values())))
    elif change == 'other_removal': new.pop(next(iter(new)))
    elif change == 'title': old[m.REMOVED]['title'] = '关于实施风险警示的公告'
    elif change == 'category': old[m.REMOVED]['columns'][0]['column_code'] = '001002004003'
    elif change == 'common_change': new[next(iter(new))]['notice_date'] = '2026-11-05'
    else: new[m.REMOVED] = old[m.REMOVED]
    with pytest.raises(ValueError): review(old, new)


def test_native_risk_or_selected_event_cannot_be_dismissed():
    with pytest.raises(ValueError): review(PRIOR, CURRENT, classify=lambda title: {'other': True})
    with pytest.raises(ValueError): review(PRIOR, CURRENT, select=lambda row, symbol: {'risk': True})


@pytest.mark.parametrize('stamp', ['2026-11-04T16:00:00+00:00', '2026-11-05T06:59:59+00:00'])
def test_actual_cross_midnight_window(stamp): m.window(datetime.fromisoformat(stamp))


@pytest.mark.parametrize('stamp', ['2026-11-04T15:59:59+00:00', '2026-11-05T07:00:00+00:00', '2026-11-06T00:00:00+08:00', '2026-11-05T00:00:00'])
def test_expired_or_naive_window_rejected(stamp):
    with pytest.raises(ValueError): m.window(datetime.fromisoformat(stamp))


def test_actual_three_csv_native_manifest_and_source_disclosure():
    source = EVIDENCE / 'publication_only_41857830686'
    manifest = json.loads((source / 'top100_delivery_manifest.json').read_bytes())
    meta = m.metadata_from_verified(source, manifest)
    assert meta['subject'].startswith('[收盘确认]') and '[未发送]' not in meta['subject']
    assert meta['signal_date'] == '2026-11-04' and '实际发送于 2026-11-05' in meta['body']
    assert '不据此宣称注销已完成' in meta['body']
    manifest['streams']['microcap_top100_mom16_biweekly_live_v2_0_latest_signal.csv']['sha256'] = '0' * 64
    with pytest.raises(ValueError): m.metadata_from_verified(source, manifest)


@pytest.mark.parametrize('suffix', ['', '-send-intent', '-smtp-accepted'])
def test_existing_normal_send_evidence_prevents_another_send(monkeypatch, suffix):
    import sys
    sys.path.insert(0, str(ROOT / 'scripts'))
    import check_microcap_delivery as gate
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'workflow_dispatch')
    monkeypatch.setenv('GITHUB_REPOSITORY', m.AUTO)
    monkeypatch.setenv('GITHUB_TOKEN', 'test-not-a-credential')
    monkeypatch.setattr(m, 'window', lambda now: None)
    marker = gate.delivery_marker_name(m.date(2026, 11, 4), 'close_confirmed')
    monkeypatch.setattr(gate, 'fetch_artifacts', lambda repo, token, name: {'artifacts': [{'name': name, 'expired': False}] if name == marker + suffix else []})
    with pytest.raises(ValueError): m.delivery_gate()


def test_no_main_schedule_or_recalculation_in_recovery_workflow():
    import yaml
    document = yaml.safe_load((ROOT / '.github/workflows/microcap-realtime-digest.yml').read_text())
    event = document.get('on', document.get(True))
    assert set(event) == {'workflow_dispatch'}
    assert document['concurrency']['group'] == 'microcap-realtime-digest'
    steps = document['jobs']['send']['steps']
    commands = '\n'.join(s.get('run', '') for s in steps)
    assert 'refresh-all' not in commands and 'top100_cloud_delivery.py' not in commands
    assert 'send_report.py artifacts/metadata.json --receipt artifacts/smtp-accepted.json' in commands
    assert [s['name'] for s in steps if s.get('id') in {'send_intent','send_gmail','smtp_receipt'}] == ['Persist normal-send intent before SMTP','Send Gmail','Preserve accepted SMTP receipt']
