"""One-off delivery of the authenticated, completed Nov4 close; never recompute state.

This controller is manual-only and expires at Nov5 15:00 Beijing. It retains
the normal Nov4 intent/SMTP/marker names, original sender and all source gates.
It adjudicates only one exact non-ST catalog disappearance, without changing
the approved archive, historical authority, economic results, or timestamps.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

DAY = '2026-11-04'
R = '14b896a0915f700e5b999ff6c47676e6f8acaca1'
PACKAGE = 'df235b54c0d90cab49bed1c963cd6498f9a97e07d4a15ed83587c03841abc8d6'
REMOVED = 'AN202611041831417267'
BJ = ZoneInfo('Asia/Shanghai')
REPO = 'liuruojiang/microcap'
AUTO = 'liuruojiang/codex-daily-automation-probe'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def window(now):
    require(now.tzinfo is not None, 'aware clock required')
    local = now.astimezone(BJ)
    require(local.date() == date(2026, 11, 5) and local.hour < 15,
            'one-off Nov4 delivery window expired')


def gh(*args):
    return subprocess.check_output(['gh', *args], timeout=300)


def api(repo, endpoint):
    return json.loads(gh('api', f'repos/{repo}/{endpoint}'))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def source_artifact(out, run, head, name):
    run_api = api(REPO, f'actions/runs/{run}')
    require(run_api['head_sha'] == head and run_api['conclusion'] == 'success', 'source run identity')
    listing = api(REPO, f'actions/runs/{run}/artifacts?per_page=100')
    matches = [a for a in listing['artifacts'] if a['name'] == name and not a['expired']]
    require(len(matches) == 1 and matches[0]['workflow_run']['id'] == run, 'source artifact identity')
    destination = out / str(run)
    require(not destination.exists(), 'refuse stale artifact directory')
    gh('run', 'download', str(run), '--repo', REPO, '--name', name, '--dir', str(destination))
    save(out / f'{run}-api.json', {'run': run_api, 'artifact': matches[0]})
    return destination


def exact_notice_review(previous, current, classify, select, precision):
    require(set(previous) - set(current) == {REMOVED}, 'unreviewed removed notice')
    require(not set(current) - set(previous), 'unreviewed added notice')
    removed = previous[REMOVED]
    require(removed['title'] == '002639雪人集团投资者关系管理信息20261104'
            and removed['notice_date'] == '2026-11-04 00:00:00'
            and removed['columns'] == [{'column_code': '050003', 'column_name': '调研活动'}]
            and len(removed['codes']) == 1 and removed['codes'][0]['stock_code'] == '002639'
            and removed['codes'][0]['ann_type'] == 'INV', 'removed record scope changed')
    require(classify(removed['title']) == {} and select(removed, '002639') is None,
            'removed record influences native ST selection')
    # Original native same-day <1s/no-close-crossing guard, not a relaxed comparison.
    result = precision({k: v for k, v in previous.items() if k != REMOVED}, current)
    return {'decision': 'exact_non_st_transport_disappearance_no_history_adoption',
            'removed_id': REMOVED, 'removed_native_row': removed,
            'removed_native_row_sha256': sha(json.dumps(removed, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()),
            'native_selector': None, 'native_risk_actions': {}, 'common_rows_precision': result,
            'economic_results_changed': False, 'authority_changed': False}


def metadata_from_verified(source, manifest):
    revisions = {'v2_0': 'plain_mom16_fixed1_20260904',
                 'v2_3': 'plain_lb25_hl2p5_r2off_no_overheat_20261029',
                 'v2_5': 'plain_lb20_hl3_entry0_exit0_20260905'}
    require(manifest['ok'] is True and manifest['errors'] == []
            and manifest['scope'] == 'whole_workspace_delivery' and manifest['expected_date'] == DAY,
            'native whole manifest failed')
    for version, revision in revisions.items():
        name = f'microcap_top100_mom16_biweekly_live_{version}_latest_signal.csv'
        raw = (source / name).read_bytes()
        require(sha(raw) == manifest['streams'][name]['sha256'], 'CSV/native manifest binding')
        rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
        require(len(rows) == 1, 'one final signal required')
        row = rows[0]
        require(row['date'] == DAY and row['strategy_revision'] == revision, 'signal identity/date')
        require(row['official_close_confirmed_signal'] == 'True'
                and row['signal_timing'] == 'close_confirmed', 'official close identity')
        require(row['current_holding'] == row['next_holding'] == 'cash', 'unexpected reviewed holding')
    meta = json.loads((source / 'metadata.json').read_bytes())
    require(meta['status'] == 'OK' and meta['signal_date'] == DAY
            and meta['publication_mode'] == 'close_confirmed'
            and not meta.get('delivery_test_id') and meta.get('attachment') is None,
            'normal finalized metadata required')
    require(meta['subject'].startswith('[修复验证][未发送][收盘确认]') and R in meta['body'], 'producer consumer identity')
    for text in ('603028', '28514.52', '28483.52', '不据此宣称注销已完成'):
        require(text in meta['body'], 'approved share discrepancy omitted')
    meta['subject'] = meta['subject'].removeprefix('[修复验证][未发送]')
    meta['body'] += '\n\n## 延迟补发说明\n\n本邮件补发的是 2026-11-04 已完成并通过整体校验的收盘研究日报，实际发送于 2026-11-05；不是 11 月 5 日新行情信号。此前异常通知未包含有效目标，以本次通过校验的收盘结果为准。公告目录复核发现雪人集团一条投资者调研记录被来源移除，经原生 ST 选取规则核验不影响 ST 状态或本次结果；原历史及数据包未改写。\n'
    return meta


def prepare(root, out):
    window(datetime.now(timezone.utc))
    require(os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch', 'manual recovery only')
    require(os.environ.get('GITHUB_REPOSITORY') == AUTO, 'original sender repository required')
    root = root.resolve(); out = out.resolve(); out.mkdir(parents=True, exist_ok=True)
    actual = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD']).decode().strip()
    require(actual == R, 'unchanged runtime checkout required')
    sys.path.insert(0, str(root))
    from scripts.exchange_calendar import latest_completed_session
    require(latest_completed_session().isoformat() == DAY, 'formal completed session changed')
    published = source_artifact(out, 41857830686, 'a1fc7bb1af95b83a516f576eb27c5ae5a737a7a8',
                                'microcap-postclose-publication-only-20261104')
    diagnosis = source_artifact(out, 41865471442, 'fbe170fc5f2f7dff915a24d3cf16d7f14dc81d9c',
                                'microcap-notice-boundary-comparison-20261104')
    identity = json.loads((published / 'package_identity.json').read_bytes())
    receipt = json.loads((published / 'new_state_release.json').read_bytes())
    require(identity['sha256'] == receipt['remote_readback_sha256'] == PACKAGE
            and identity['bytes'] == 612766808 and identity['strategy_sha'] == R
            and receipt['all_native_data_and_consumer_gates_passed'] is True,
            'completed package provenance')
    native = api(REPO, 'actions/runs/41845742478')
    require(native['head_sha'] == '4f6a416477ac06a707d36446805b82bc19590c12', 'native producer identity')
    jobs = api(REPO, 'actions/runs/41845742478/jobs?per_page=100')
    steps = {s['name']: s['conclusion'] for j in jobs['jobs'] if j['name'] == 'build' for s in j['steps']}
    for name in ('Official strict incremental whole refresh and three-version generation',
                 'Independent official whole delivery check', 'Query official close-confirmed v2.0',
                 'Query official close-confirmed v2.3', 'Query official close-confirmed v2.5',
                 'Official whole pack with original validations',
                 'Verify actual source authority disclosures from native chain',
                 'Render and validate normal consumer without SMTP'):
        require(steps.get(name) == 'success', 'native gate incomplete: ' + name)
    failed_jobs = api(AUTO, 'actions/runs/41859237718/jobs?per_page=100')
    byname = {j['name']: j for j in failed_jobs['jobs']}
    require(byname['regression / strategy-tests (microcap)']['conclusion'] == 'success'
            and byname['regression / automation-tests']['conclusion'] == 'success', 'accepted full regression missing')
    old_send = byname['send']
    require(old_send['status'] == 'completed' and old_send['conclusion'] == 'failure', 'previous writer not ended')
    old_steps = {s['name']: s['conclusion'] for s in old_send['steps']}
    require(old_steps.get('Restore approved release fallback') == 'success', 'actual receiving restore incomplete')
    for name in ('Persist normal-send intent before SMTP', 'Preserve accepted SMTP receipt', 'Mark digest delivered'):
        require(old_steps.get(name) == 'skipped', 'previous normal delivery uncertain')
    save(out / 'prior-native-and-receiver-gates.json', {'native_run': native, 'native_jobs': jobs, 'receiver_jobs': failed_jobs})
    from scripts.st_notice_incremental import collect_days
    from scripts.st_notice_refresh import _native, _precision_drift_only
    from scripts.st_notice_evidence import _registry, is_equity_issuer
    from scripts.st_history_scope import _daily_notice_row
    import microcap_top100_mom16_biweekly_live_v2_0 as base
    previous = json.loads((diagnosis / 'prior_native_rows.json').read_bytes())
    started = datetime.now(timezone.utc)
    capture = out / 'fresh-notice-observation'
    collect_days(capture, DAY, DAY, required_asof=started)
    current = _native(capture / DAY, DAY)
    registry, _ = _registry(root)
    require(REMOVED not in registry, 'removed record has approved ST body evidence')
    review = exact_notice_review(previous, current, base.freq_mod.classify_st_notice_risk_actions,
                                lambda row, symbol: (_daily_notice_row(row, symbol, DAY, registry)
                                                     if is_equity_issuer(row, symbol) else None), _precision_drift_only)
    review.update(started_at=started.isoformat(), completed_at=datetime.now(timezone.utc).isoformat())
    save(out / 'exact-notice-review.json', review)
    manifest = json.loads((published / 'top100_delivery_manifest.json').read_bytes())
    meta = metadata_from_verified(published, manifest)
    meta['completed_close_resume'] = {'revision': 'verified_completed_close_20261104_v1',
        'signal_date': DAY, 'package_sha256': PACKAGE, 'producer_run': 41845742478,
        'publication_run': 41857830686, 'notice_review_sha256': sha((out / 'exact-notice-review.json').read_bytes()),
        'actual_prepared_at': datetime.now(timezone.utc).isoformat()}
    save(out / 'metadata.json', meta)
    window(datetime.now(timezone.utc))
    print('Completed close and current exact ST-source review passed; no state recalculation', flush=True)


def delivery_gate():
    window(datetime.now(timezone.utc))
    require(os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
            and os.environ.get('GITHUB_REPOSITORY') == AUTO, 'manual original sender required')
    import check_microcap_delivery as gate
    marker = gate.delivery_marker_name(date.fromisoformat(DAY), 'close_confirmed')
    token = os.environ['GITHUB_TOKEN']
    # Every normal dedupe namespace is checked, including unknown intents.
    for suffix in ('', '-smtp-accepted', '-send-intent'):
        payload = gate.fetch_artifacts(AUTO, token, marker + suffix)
        require(not gate.marker_exists(payload, marker + suffix), 'existing normal send evidence; reconcile, never resend')
    legacy = gate.delivery_marker_name(date.fromisoformat(DAY))
    require(not gate.marker_exists(gate.fetch_artifacts(AUTO, token, legacy), legacy), 'legacy delivery exists')
    gate.write_outputs({'should_send': 'true', 'marker_name': marker, 'delivery_date': DAY})


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('mode', choices=['prepare', 'gate'])
    p.add_argument('--root', type=Path); p.add_argument('--out', type=Path)
    a = p.parse_args()
    if a.mode == 'gate': delivery_gate()
    else: prepare(a.root, a.out)
