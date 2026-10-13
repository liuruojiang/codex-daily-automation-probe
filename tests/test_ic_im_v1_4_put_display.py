"""Real no-send report fields and clearly labeled synthetic missing-field cases."""
import copy
import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build_ic_im_v1_4_digest as digest
import ic_im_v1_4_put_display as display


def actual():
    fixture = ROOT / 'tests/fixtures/icim/readiness_20261009'
    data = (fixture / 'result.json').read_bytes()
    provenance = json.loads((fixture / 'provenance.json').read_text())
    assert provenance['source_run_id'] == 37911491710
    assert hashlib.sha256(data).hexdigest() == provenance['sha256']
    return json.loads(data)


@pytest.mark.parametrize('product', ['IC', 'IM'])
def test_real_put_tables_present_in_both_mail_formats_without_changing_result(product):
    result = actual()
    before = copy.deepcopy(result)
    subject, body, action = digest.build_success(result, '', '')
    rendered = digest.build_success_html(result, '')
    assert result == before
    assert action is False
    for text in ('Put 档位、期货对应数量与行权价', '当前与下一交易日规范化数量', '每1张期货对应Put', '当前Put与下一目标Put合约证据', '规范化小数张数', '实际账户整数张数N/A'):
        assert text in body and text in rendered
    section = display.markdown(product, result['signals'][product])
    assert '2026-10-08' in section and '2026-10-12' in section
    assert '网格 | 0 / 0 | 0 / 0' in section
    assert '数量0' in section
    if product == 'IC':
        assert '510500P2612M07500' in section and '2026-12 / 7.5' in section
        assert '20张 / 每1张IC' in section
        assert '本次动态重算N/A' in section
    else:
        assert 'MO2612-P-7600' in section and '2026-12 / 7600' in section
        assert '3张 / 每1张IM' in section
        assert '独立生命周期核心Put：0张；普通核心保护当前1.5张' in section


def test_other_iv_contract_and_wrong_leg_quote_cannot_fill_put_quote_evidence():
    s = actual()['signals']['IM']
    s['put_market'] = '`MO2612-P-6700`，最新 123'
    s['put_pricing_evidence'] = {}
    table = display.markdown('IM', s)
    assert '6700' not in table
    assert '123' not in table
    assert 'N/A（result未保存该合约报价原文）' in table
    assert '不借用IV参考合约' in table


def test_missing_contract_and_zero_future_ratio_are_explicit_na():
    s = actual()['signals']['IC']
    s['v14_core_put_current_contract'] = None
    s['total_units_current'] = 0
    table = display.markdown('IC', s)
    assert 'N/A（result合约缺失）' in table
    assert 'N/A（期货为空仓或数量缺失）' in table


def test_historical_rendering_has_no_added_rows_or_blank_table():
    s = actual()['signals']['IM']
    s['market_date'] = '2026-10-08'
    assert display.evidence('IM', s) == []
    assert display.markdown('IM', s) == ''
    assert display.html_tables('IM', s) == ''
    assert '<tr><td></td></tr>' not in digest.product_card('IM', s, False)


@pytest.mark.parametrize('contract', [None, 'P-OLD', 'MO2613-P-7600', '510500P2613M07500'])
def test_unverified_contract_encoding_never_invents_strike_or_month(contract):
    assert display.contract_terms(contract).startswith('N/A')


CORE = '510500P2612M07500'
MOMENTUM = '510500P2612M07300'
NEW_CORE = '510500P2701M07200'


def split_signal():
    """Synthetic identity fault injection over the hash-checked real result."""
    s = actual()['signals']['IC']
    s.update(v14_core_put_current_contract=CORE, v14_core_put_contract=CORE,
             put_current_contract=MOMENTUM, put_target_contract=MOMENTUM,
             put_current_core_qty=10, put_target_core_qty=10,
             put_current_momentum_qty=3, put_target_momentum_qty=3,
             put_current_total_qty=13, put_target_total_qty=13,
             put_market=f'`{MOMENTUM}`，最新 0.25；买/卖 0.24/0.26',
             put_pricing_evidence={})
    return s


def contract_rows(s):
    table = next(item for item in display.evidence('IC', s)
                 if item[0] == '当前Put与下一目标Put合约证据')
    return {row[0]: row for row in table[2]}


def test_split_ic_contracts_keep_separate_rows_and_do_not_borrow_momentum_quote():
    s = split_signal()
    before = copy.deepcopy(s)
    rows = contract_rows(s)
    assert rows['当前 核心'][1:3] == ['10', CORE]
    assert rows['下一目标 核心'][1:3] == ['10', CORE]
    assert rows['当前 动量'][1:3] == ['3', MOMENTUM]
    assert rows['下一目标 动量'][1:3] == ['3', MOMENTUM]
    assert 'N/A（result未保存该合约报价原文）' in rows['当前 核心'][4]
    assert '最新 0.25' in rows['当前 动量'][4]
    assert s == before


@pytest.mark.parametrize('status', ['monthly', 'confirmed_3x', 'ordinary_pending'])
def test_updated_target_identity_does_not_replace_saved_current_core(status):
    s = split_signal()
    s.update(v14_core_put_contract=NEW_CORE, put_target_core_qty=12,
             put_target_total_qty=15)
    if status == 'monthly':
        s.update(option_monthly_reset_due=True, put_target_contract=NEW_CORE)
    elif status == 'confirmed_3x':
        s.update(v14_profit_reentry_status='confirmed_open_research_price',
                 v14_profit_open_executed_contract=NEW_CORE,
                 v14_profit_open_executed_qty=12)
    else:
        s.update(v14_ordinary_put_plan_status='scheduled_t_plus_1_open',
                 v14_ordinary_put_pending={'legs': {'core': {
                     'old_contract': CORE, 'old_qty': 10,
                     'new_contract': NEW_CORE, 'new_qty': 12}}})
    rows = contract_rows(s)
    assert rows['当前 核心'][1:3] == ['10', CORE]
    assert rows['下一目标 核心'][1:3] == ['12', NEW_CORE]


def test_pending_3x_replacement_is_not_the_ordinary_or_held_core():
    s = split_signal()
    s.update(v14_profit_pending=True, v14_profit_reentry_contract=NEW_CORE,
             v14_profit_reentry_qty=12,
             v14_profit_reentry_status='scheduled_t_plus_1_open')
    rows = contract_rows(s)
    assert rows['当前 核心'][2] == rows['下一目标 核心'][2] == CORE
    assert NEW_CORE not in display.markdown('IC', s)


def test_initial_core_target_uses_shared_selection_without_inventing_current_holding():
    s = split_signal()
    s.update(v14_core_put_current_contract=None, v14_core_put_contract=None,
             put_current_core_qty=0, put_target_contract=NEW_CORE)
    rows = contract_rows(s)
    assert rows['当前 核心'][2] == '无（数量0）'
    assert rows['下一目标 核心'][2] == NEW_CORE


@pytest.mark.parametrize('leg', ['core', 'momentum'])
@pytest.mark.parametrize('when', ['current', 'target'])
def test_zero_quantity_never_reuses_a_stale_contract(leg, when):
    s = split_signal()
    s[f'put_{when}_{leg}_qty'] = 0
    label = ('当前' if when == 'current' else '下一目标') + (' 核心' if leg == 'core' else ' 动量')
    row = contract_rows(s)[label]
    assert row[2:5] == ['无（数量0）'] * 3


def test_missing_independent_current_core_is_na_even_with_a_momentum_contract():
    s = split_signal()
    s.pop('v14_core_put_current_contract')
    assert contract_rows(s)['当前 核心'][2] == 'N/A（result合约缺失）'
