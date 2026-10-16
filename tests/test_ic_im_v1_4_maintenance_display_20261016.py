"""Real saved monthly maintenance result plus clearly marked synthetic guards."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import build_ic_im_v1_4_digest as digest
import ic_im_v1_4_put_display as display

FIXTURES = Path(__file__).parent / 'fixtures/icim'


def real_result():
    return json.loads((FIXTURES/'maintenance_20261016/result.json').read_text(encoding='utf-8'))


def test_real_provenance_and_candidate_identity():
    source = FIXTURES/'maintenance_20261016/result.json'
    provenance = json.loads((source.parent/'provenance.json').read_text(encoding='utf-8'))
    assert provenance['run_id'] == '38853603565'
    assert hashlib.sha256(source.read_bytes()).hexdigest() == provenance['sha256']
    signal = real_result()['signals']['IC']
    assert signal['v14_core_put_contract'] == '510500P2612M07500'
    assert signal['put_target_contract'] == '510500P2612M06500'
    assert display.ic_contract(signal, 'current', 'core') == '510500P2612M07500'
    assert display.ic_contract(signal, 'target', 'core') == '510500P2612M06500'
    assert '17张' in digest.put_text('IC', signal, True)
    assert '510500P2612M07500' not in digest.put_text('IC', signal, True)


def test_real_builder_plain_html_separate_futures_and_today_put_without_mutation():
    item = real_result()
    before = deepcopy(item)
    subject, plain, _ = digest.build_success(item, '', '')
    html = digest.build_success_html(item, '')
    assert '月度Put需维护' in subject
    for text in (plain, html):
        assert text.count('今日（2026-10-16）') == 2
        assert text.count('2026-10-16 当天收盘完成换仓') == 2
        assert '期货：下一交易日2026-10-19研究目标' in text
        assert 'Put：2026-10-16收盘维护候选（盘尾复核）' in text
        assert '下一交易日2026-10-19研究目标 期货 / Put' not in text
        assert '当日维护候选 核心' in text
        assert '2026-10-16收盘维护候选，盘尾复核：合计17张（核心17张 510500P2612M06500' in text
        assert '2026-10-16收盘维护候选，盘尾复核：合计1.5张（核心1.5张 MO2612-P-6900' in text
        assert '不提前锁定新合约' in text
    assert item == before


def test_synthetic_pending_3x_stays_separate_from_monthly_candidate():
    signal = real_result()['signals']['IC']
    signal['v14_profit_reentry_status'] = 'scheduled_t_plus_1_open'
    signal['v14_profit_reentry_contract'] = 'SYNTHETIC_3X_REPLACEMENT'
    signal['v14_profit_reentry_pending'] = {'new_contract': 'SYNTHETIC_3X_REPLACEMENT'}
    assert display.ic_contract(signal, 'target', 'core') == signal['put_target_contract']
    assert 'SYNTHETIC_3X_REPLACEMENT' not in digest.put_text('IC', signal, True)
    assert signal['v14_core_put_contract'] == '510500P2612M07500'


def test_synthetic_wrong_node_cannot_select_monthly_candidate():
    signal = real_result()['signals']['IC']
    signal.update(market_date='2026-10-19', next_trade_date='2026-10-20',
                  option_monthly_reset_execution_date='2026-10-19')
    assert not display.monthly_candidate(signal)
    assert display.ic_contract(signal, 'target', 'core') == signal['v14_core_put_contract']


def test_saved_oct09_render_hashes_unchanged():
    provenance = json.loads((FIXTURES/'maintenance_20261016/provenance.json').read_text(encoding='utf-8'))
    item = json.loads((FIXTURES/'readiness_20261009/result.json').read_text(encoding='utf-8'))
    subject, plain, _ = digest.build_success(item, '', '')
    html = digest.build_success_html(item, '')
    actual = {k: hashlib.sha256(v.encode('utf-8')).hexdigest()
              for k, v in dict(subject=subject, plain=plain, html=html).items()}
    assert actual == provenance['oct09_render_sha256_before_patch']
