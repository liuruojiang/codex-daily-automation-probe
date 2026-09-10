"""Frozen public feed evidence from run 34414949679; no mail/account data."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import daily_reports as d


def test_real_multi_asset_and_etf_candidates_reach_evidence_ranking():
    rows = json.loads((Path(__file__).parent / "fixtures/etf_20260910_public_candidates.json").read_text(encoding="utf-8"))
    for row in rows:
        item = d.Item(**row)
        assert d.etf_research_relevant(item), item.title
        assert d.score_etf_research_item(item) is not None, item.title
        assert d.etf_has_enough_summary_evidence(item), item.title


def test_single_company_noise_stays_excluded():
    item = d.Item("test", "Rocket Labs shares surge", "https://example.test/rocket", "2026-09-09T04:00:00Z", "Shares rise following a launch.")
    assert not d.etf_research_relevant(item)
