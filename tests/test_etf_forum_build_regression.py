from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import daily_reports as dr
from validate_etf_delivery import validate


class EtfForumBuildRegressionTests(unittest.TestCase):

    def item(self, suffix, engagement):
        source = "Bogleheads.org Forum (" + engagement + ")"
        return dr.Item(source, 'SCHG vs QQQM for long term ' + suffix, 'https://www.bogleheads.org/forum/viewtopic.php?t=' + suffix, '2026-09-06T12:00:00Z', 'Portfolio allocation question comparing SCHG and QQQM, overlap, concentrated growth exposure, long-term core, rebalance.')

    def test_real_forum_selector_rejects_invalid_engagement_without_losing_valid_order(self):
        malformed = [self.item('bad' + str(i), text) for i, text in enumerate(['comments, others replied', 'score ,; comments/replies ,; views ,', 'score/upvotes ,,,; comments/replies ,,,; views ,,,'])]
        high = self.item('high', 'comments/replies 100; views 1000')
        low = self.item('low', 'comments/replies 30; views 1000')
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            previous = Path.cwd()
            os.chdir(temp)
            stack.callback(os.chdir, previous)
            stack.enter_context(patch.object(dr, 'enrich_article_item', side_effect=lambda x: x))
            picked = dr.select_etf_forum_items([*malformed, low, high], limit=8)
        self.assertEqual([i.url for i in picked], [high.url, low.url])

    def test_real_build_with_malformed_forum_keeps_body_manifest_history_consistent(self):
        cutoff = datetime(2026, 9, 7, 5, 0, tzinfo=dr.BJ)
        research = [dr.Item('Independent fixture', f'Portfolio risk study {i}', f'https://example.com/study-{i}', '2026-09-06T12:00:00Z', f'Distinct study {i} investigates portfolio risk constraints and rebalancing.') for i in range(6)]
        scored = [dr.ScoredResearchItem(i, 90, '学术研究', '风险研究', (dr.QUANT_STRATEGY_SECTION,), ('fixture',)) for i in research]
        feed = dr.ResearchFeed('Independent fixture', 'https://example.com/feed', '学术研究', '风险研究', (dr.QUANT_STRATEGY_SECTION,), 10)
        bad = self.item('bad-build', 'comments, views ,')
        good = self.item('good-build', 'comments/replies 100; views 1000')
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            previous = Path.cwd()
            os.chdir(temp)
            stack.callback(os.chdir, previous)
            Path('digest_history').mkdir()
            Path('digest_history/etf.json').write_text(json.dumps({'items': []}), encoding='utf-8')
            stack.enter_context(patch.dict(os.environ, {'GITHUB_RUN_ID': 'independent-adversarial-fixture', 'GITHUB_SHA': 'fixture-not-production'}))
            stack.enter_context(patch.object(dr, 'now_bj', return_value=cutoff))
            stack.enter_context(patch.object(dr, 'ETF_RESEARCH_FEEDS', (feed,)))
            for name in ('ETF_FIXED_MONITOR_FEEDS', 'ETF_FIXED_PAGE_MONITORS', 'ETF_FORUM_SUBREDDITS', 'ETF_EXTERNAL_FORUM_FEEDS'):
                stack.enter_context(patch.object(dr, name, ()))
            stack.enter_context(patch.object(dr, 'parse_feed', return_value=research))
            stack.enter_context(patch.object(dr, 'select_etf_research_items', return_value=scored))
            stack.enter_context(patch.object(dr, 'collect_etf_forum_items', return_value=[bad, good]))
            stack.enter_context(patch.object(dr, 'enrich_article_item', side_effect=lambda x: x))
            out = Path(temp) / 'artifacts'
            dr.build_etf(out)
            metadata = json.loads((out / 'metadata.json').read_text(encoding='utf-8'))
            manifest = json.loads((out / 'collection_manifest.json').read_text(encoding='utf-8'))
            history = json.loads(Path('digest_history/etf.json').read_text(encoding='utf-8'))
            self.assertEqual(validate(metadata, manifest), [])
            self.assertNotIn(bad.url, metadata['body'])
            self.assertIn(good.url, metadata['body'])
            self.assertNotIn(bad.url, {i['url'] for i in history['items']})
            self.assertIn(good.url, {i['url'] for i in history['items']})
            self.assertEqual(
                {i['url'] for i in history['items']},
                {i['url'] for i in manifest['selected_items']},
            )


if __name__ == "__main__":
    unittest.main()
