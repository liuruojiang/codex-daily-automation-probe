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
    s['put_current_contract'] = None
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
