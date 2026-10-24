"""Regressions for the content audit fixes; no network or model calls."""
import copy
import json
from pathlib import Path
import sys
import unittest
from html.parser import HTMLParser

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import daily_reports as daily
import etf_movers as movers
import etf_fresh_shadow as fresh
import etf_mail_replace_preview as replacement
from test_etf_movers import row
from test_etf_mail_preview import fixture as mail_fixture
from test_etf_fresh_shadow import fixture as fresh_fixture


class ContentRepairTests(unittest.TestCase):
    def test_html_compaction_preserves_all_text_structure_links_and_nonclass_attributes(self):
        class Signature(HTMLParser):
            def __init__(self): super().__init__(); self.events = []; self.style = False
            def handle_starttag(self, tag, attrs):
                self.style = tag == 'style' or self.style
                self.events.append(('start', tag, [(k, v) for k, v in attrs if k != 'class']))
            def handle_endtag(self, tag):
                self.events.append(('end', tag))
                if tag == 'style': self.style = False
            def handle_data(self, data):
                if not self.style: self.events.append(('data', data))
        body = '## 保留目录目标\n\n| 警报 | 引文 |\n|---|---|\n| FAILED | report-td |\n\n- [原文](https://example.org/report-td)\n\n> 原文引句（Q1 / p1）：report-p 原句 [x](https://example.org/report-li)'
        before = daily.markdown_to_email_html(body, '警报全部保留 report-th')
        after = daily.compact_email_html_classes(before)
        left, right = Signature(), Signature()
        left.feed(before); right.feed(after)
        self.assertEqual(left.events, right.events)
        self.assertIn('.rt{', after)
        self.assertIn('class="rt"', after)
        self.assertIn('href="https://example.org/report-td"', after)
        self.assertIn('<main class="email-main">', after)
        self.assertNotIn('.report-td{', after)
        self.assertEqual(daily.compact_email_html_classes(after), after)
        self.assertEqual(daily.expand_email_html_classes(after), before)
        css = '<style>.report-td-extra{content:".report-td"}.report-td{content:".report-th"}</style>'
        self.assertEqual(daily.compact_email_html_classes(css), '<style>.report-td-extra{content:".report-td"}.rt{content:".report-th"}</style>')

    def test_gasoline_and_natural_gas_are_distinct_and_both_survive_ranking(self):
        products = [row('UGA', 'United States Gasoline Fund LP', change=-1.64),
                    row('UNG', 'United States Natural Gas Fund LP', change=-1.5)]
        self.assertEqual([movers.theme_key(p) for p in products], ['gasoline_futures', 'natural_gas_futures'])
        self.assertEqual(len({movers.dedupe_family(p, movers.theme_key(p)) for p in products}), 2)
        records = [movers._record(p, '2026-10-20', p['regularMarketChangePercent']) for p in products]
        self.assertEqual(records[0]['name'], '汽油期货 ETF')
        self.assertNotIn('天然气', records[0]['description'])
        self.assertEqual(records[1]['name'], '天然气期货 ETF')
        self.assertEqual({p['symbol'] for p in movers._rank(records, False, 10)}, {'UGA', 'UNG'})

    def test_fresh_summary_uses_this_snapshot_not_prior_sample_numbers(self):
        inputs, record, old, index = fresh_fixture()
        report, page = fresh.build(*inputs[1:], record, old, index)
        for expected in ('快照截止 2026-10-20T00:00:00+00:00', '2条选中，替换2篇，保留0条原文',
                         '正式采集记录123', '2项警报、0处缺口', '供应商token总量未记录'):
            self.assertIn(expected, page)
        for stale in ('39518483125', '9741', '32条', '132项缺口'):
            self.assertNotIn(stale, page)
        entry = copy.deepcopy(record['registry']['entries'][0])
        entry['annotation']['scope_note_zh'] = '<script>scope</script>'
        report['initial_model_usage'] = {'requests': 7, 'provider_reported_total_tokens': 99}
        page = fresh.render_summary(report, record['registry']['snapshot'], [entry], 8)
        for expected in ('7次模型请求', '供应商报告99个token', '旧登记8篇', '&lt;script&gt;'):
            self.assertIn(expected, page)
        self.assertNotIn('<script>', page)

    def test_quotes_visible_without_turning_quoted_urls_or_markup_into_links(self):
        inputs = mail_fixture()
        _, page = replacement.build(*inputs)
        quote = inputs[0]['cards'][inputs[0]['materials'][0]['material_id']]['facts'][0]['citations'][0]['quote']
        self.assertIn(quote, page)
        text = '> 原文引句（p1）：<script>x</script> https://example.org/quote [fake](https://example.org/fake)'
        rendered = daily.markdown_to_email_html(text, '')
        self.assertNotIn('<script>', rendered)
        self.assertNotIn('href="https://example.org/quote"', rendered)
        self.assertNotIn('href="https://example.org/fake"', rendered)

    def test_explanations_are_bound_to_material_and_card_revision(self):
        entries = json.loads(replacement.EXPLANATIONS.read_text(encoding='utf-8'))['entries']
        for entry in entries:
            packet = {'material_id': entry['material_id'], 'card_hash': entry['card_hash']}
            self.assertEqual(replacement.reading_explanation(packet), entry)
            packet['card_hash'] = 'changed'
            with self.assertRaisesRegex(ValueError, 'card hash mismatch'):
                replacement.reading_explanation(packet)
        self.assertIsNone(replacement.reading_explanation({'material_id': 'unknown', 'card_hash': 'unknown'}))

    def test_table_of_contents_has_unique_targets_and_safe_fragments(self):
        body = '- [策略相关 ETF / 指数涨跌](#策略相关-etf--指数涨跌)\n\n## 策略相关 ETF / 指数涨跌\n\n## 策略相关 ETF / 指数涨跌'
        page = daily.markdown_to_email_html(body, '')
        self.assertIn('href="#策略相关-etf--指数涨跌"', page)
        self.assertEqual(page.count('id="策略相关-etf--指数涨跌"'), 1)
        self.assertIn('id="策略相关-etf--指数涨跌-1"', page)
        self.assertNotIn('href="javascript:', daily.email_inline_markdown('[x](javascript:alert)'))
