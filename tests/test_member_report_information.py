"""Presentation counterexamples; synthetic saved inputs, no market collection."""
import copy
import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
from build_microcap_member_digest import render, saved_context, REVISIONS

def sample():
    return dict(schema='member_daily_result_v1',market_date='2026-11-06',publication_mode='close_confirmed',
        anchor_date='2026-11-05',generated_at='2026-11-06T16:01:00+08:00',quote_coverage='100/100',period_status='current',warnings=[],
        versions=[dict(version=v,revision=r,status='verified',signal=dict(strategy_revision=r,quote_trade_date='2026-11-06',
            date='2026-11-06',signal_timing='close_confirmed',official_close_confirmed_signal=True,
            quote_coverage='100/100',current_holding='cash',next_holding='cash',current_execution_scale=0,next_session_target_scale=0,
            momentum_gap=-.03,annualized_log_wls_score=-1.06,microcap_mom=-.05,hedge_mom=-.02,
            entry_threshold=0,exit_threshold=0,momentum_gap_entry_threshold=0,momentum_gap_exit_buffer=.08)) for v,r in REVISIONS.items()])

def test_restored_information_is_display_only_and_scores_are_not_returns():
    value=sample();before=copy.deepcopy(value)
    context=dict(checkpoint=dict(as_of_date='2026-11-06',next_trade_date='2026-11-09'),period=dict(anchor_day='2026-10-29',
        effective_from='2026-10-30',next_rebalance_date='2026-11-12',rebalance_events=[dict(rebalance_date='2026-10-29',entry_count=12,exit_count=12,blocked_entry_count=2,blocked_exit_count=2)]))
    report=render(value,expected_date='2026-11-06',context=context)
    for text in ['2026-11-09','100/100','动量差','等于0保持','8.00%','-106.00%','不是实际收益率','受阻换入2','不是今天']:
        if text=='不是今天': assert '不自动成为今天的买卖动作' in report['body']
        else: assert text in report['body']
    assert '<table ' in report['html_body'] and value==before

def test_cash_with_redundant_scale_flag_does_not_create_action():
    value=sample()
    for row in value['versions']: row['signal']['scale_trade_required']=True
    body=render(value,expected_date='2026-11-06')['body']
    assert '持仓相同，尺度调整' not in body and '维持空仓，无需操作' in body

def test_nonfinite_or_conflicting_scale_does_not_become_a_trade():
    value=sample();value['versions'][0]['signal']['current_execution_scale']=float('nan')
    body=render(value,expected_date='2026-11-06')['body']
    assert '动作未确认' in body and 'N/A' in body

def test_optional_context_tamper_is_a_warning_not_a_whole_report_block(tmp_path):
    (tmp_path/'period.json').write_text('{}');(tmp_path/'history.csv').write_text('date,microcap,hedge\n')
    cp=dict(as_of_date='2026-11-06',period=dict(path='period.json',sha256='0'*64),history=dict(path='history.csv',sha256='0'*64))
    (tmp_path/'checkpoint.json').write_text(json.dumps(cp))
    context=saved_context(tmp_path)
    assert context['presentation_warnings'] and 'period' not in context
    assert render(sample(),expected_date='2026-11-06',context=context)['status']=='OK'


def test_wrong_day_optional_quote_context_does_not_supply_today_prices(tmp_path):
    (tmp_path/'hedge.json').write_text(json.dumps(dict(market_date='2026-11-05',close=123,source='old source')))
    context=saved_context(tmp_path,expected_date='2026-11-06')
    assert 'hedge' not in context and context['presentation_warnings']
    report=render(sample(),expected_date='2026-11-06',context=context)
    assert report['status']=='OK' and 'old source' not in report['body']
