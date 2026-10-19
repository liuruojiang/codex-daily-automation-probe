"""Independent audit counterexamples: validity must gate interpretation."""
import copy
import unittest

from advisory import VARIANTS, build_advisory, render_advisory_text
from test_advisory import sample


class AdvisoryAuditTests(unittest.TestCase):
    def neutral(self):
        report = sample()
        report['supplemental']['vix']['one_year_percentile'] = .4
        for item in report['core'].values():
            item.update(valuation_percentile=.5, term_spread_percentile=.5,
                        extreme_components=0, state='mid-regime')
        return report

    def test_each_cape_window_must_be_valid_and_same_month(self):
        for key in VARIANTS[:2]:
            for changes in ({'as_of': '2020-01'}, {'as_of': '2027-01'},
                            {'as_of': '2026-08'}, {'valuation_percentile': float('nan')},
                            {'term_spread_percentile': 80}, {'boundary_score_0_to_2': float('inf')},
                            {'extreme_components': True}, {'valuation_ma12': None}):
                with self.subTest(key=key, changes=changes):
                    report = self.neutral()
                    report['core'][key].update(changes)
                    result = build_advisory(report)
                    self.assertEqual(result['current_core_state'], '数据不完整')
                    self.assertIn('证据不足', result['stance'])

    def test_stale_dividend_cannot_create_current_boundary(self):
        report = self.neutral()
        for key in VARIANTS[2:]:
            report['core'][key].update(as_of='2020-01', valuation_percentile=.05,
                                       term_spread_percentile=.95, extreme_components=2)
        report['overall_state'] = '边界'
        result = build_advisory(report)
        self.assertEqual(result['current_core_state'], '中性区间')
        self.assertNotIn('极端环境', result['stance'])
        self.assertFalse(result['validity'][VARIANTS[2]]['current'])
        self.assertIn('较旧股息率仅作滞后背景', render_advisory_text(result))

    def test_future_dividend_and_missing_variant_are_explicitly_excluded(self):
        report = self.neutral()
        report['core'][VARIANTS[2]]['as_of'] = '2027-01'
        report['core'][VARIANTS[3]] = None
        result = build_advisory(report)
        self.assertFalse(result['validity'][VARIANTS[2]]['current'])
        self.assertFalse(result['validity'][VARIANTS[3]]['current'])
        self.assertIn('未来日期', render_advisory_text(result))

    def test_missing_invalid_stale_or_misaligned_vix_cannot_relax_new_risk(self):
        for changes in ({'value': None}, {'value': float('inf')},
                        {'one_year_percentile': 80}, {'one_year_percentile': float('nan')},
                        {'as_of': '2026-10-15'}, {'as_of': '2026-09-01'},
                        {'as_of': '2027-01-01'}):
            with self.subTest(changes=changes):
                report = self.neutral()
                report['supplemental']['vix'].update(changes)
                result = build_advisory(report)
                self.assertNotIn('警戒升温', result['stance'])
                self.assertIn('放缓追涨', result['sections'][2][1][0])
                self.assertIn('不能确认风险已经缓和', result['conclusion'])

    def test_percentile_only_revision_is_not_reported_as_unchanged(self):
        report = self.neutral()
        previous = copy.deepcopy(report)
        report['core'][VARIANTS[0]].update(valuation_percentile=.99, extreme_components=1, state='elevated')
        changes = build_advisory(report, previous)['sections'][1][1]
        self.assertIn('可能为历史修订', next(s for s in changes if VARIANTS[0] in s))

    def test_conflicting_current_valuation_directions_are_disclosed(self):
        report = self.neutral()
        for item in report['core'].values():
            item.update(valuation_percentile=.95, extreme_components=1)
        result = build_advisory(report)
        self.assertIn('估值方向存在分歧', result['conclusion'])
        self.assertIn('放缓追涨', result['sections'][2][1][0])

    def test_current_month_is_not_claimed_as_final_month_end(self):
        report = self.neutral()
        for item in report['core'].values():
            item['as_of'] = '2026-10'
        self.assertIn('不能视作月结确认', render_advisory_text(build_advisory(report)))

    def test_no_nan_or_impossible_negative_price_momentum_advice(self):
        for value in (float('nan'), float('inf'), -1, -2):
            report = self.neutral()
            report['supplemental']['sp500_price_momentum']['momentum_1m'] = value
            self.assertIn('证据不足', build_advisory(report)['stance'])

    def test_rules_are_not_paper_validated_and_triggers_contain_actual_margin(self):
        result = build_advisory(sample())
        text = render_advisory_text(result)
        self.assertIn('全部是研究启发式', text)
        self.assertIn('未证明能改善收益或回撤', text)
        self.assertIn('+0.28%', result['sections'][3][1][0])


if __name__ == '__main__':
    unittest.main()
