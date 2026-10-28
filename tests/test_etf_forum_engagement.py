from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import daily_reports as dr


def item(summary: str) -> dr.Item:
    return dr.Item("Bogleheads.org Forum", "Asset allocation research", "https://example.com/thread", "", summary)


@pytest.mark.parametrize("field", ["score/upvotes", "comments/replies", "views"])
@pytest.mark.parametrize("invalid", [",", ",,,", "9" * 5000], ids=["comma", "commas", "oversized"])
def test_unusable_count_never_crashes_or_displays_as_verified(field, invalid):
    value = item(f"{field} {invalid}")
    assert dr.forum_engagement_score(value) == 0
    assert dr.forum_engagement_label(value) == ""
    assert not dr.forum_item_meets_quality_bar(value)


def test_unusable_rank_never_crashes_or_displays_as_verified():
    value = item("hot rss rank " + "9" * 5000)
    assert dr.forum_engagement_score(value) == 0
    assert dr.forum_engagement_label(value) == ""


def test_natural_language_comma_does_not_hide_later_verified_count():
    value = item("Read the comments, then compare. comments/replies 42; views 1,250")
    assert dr.forum_engagement_score(value) == 151
    assert dr.forum_engagement_label(value) == "回复 42、浏览 1,250"


@pytest.mark.parametrize("summary", ["mycomments 50", "overviews 500", "myscore 900", "upvotes_total 900"])
def test_metric_label_must_be_a_whole_word(summary):
    assert dr.forum_engagement_score(item(summary)) == 0
    assert dr.forum_engagement_label(item(summary)) == ""


def test_normal_count_weights_and_original_display_are_preserved():
    value = item("score/upvotes 1,234; comments/replies 56; views 7,890; hot rss rank #2")
    expected_rank = max(0, dr.ETF_FORUM_MIN_ENGAGEMENT_SCORE + 60 - 2 * 8)
    assert dr.forum_engagement_score(value) == 1234 + 56 * 3 + 7890 // 50 + expected_rank
    assert dr.forum_engagement_label(value) == "点赞/评分 1,234、回复 56、浏览 7,890、old Reddit hot RSS rank 2"


def test_zero_is_known_and_absence_is_not_displayed():
    assert dr.forum_engagement_label(item("comments 0")) == "回复 0"
    assert dr.forum_engagement_label(item("No engagement metadata available")) == ""


def test_unicode_decimal_counts_remain_supported():
    assert dr.forum_engagement_score(item("comments ４２; views ١٠٠")) == 128


def test_sorting_unaffected_by_broken_prose_and_unknown_fields():
    valid = item("score/upvotes 90; comments/replies 20")
    polluted = item("Check comments, and views, carefully. score/upvotes 90; comments/replies 20")
    missing = item("comments, unavailable; views ,")
    assert dr.forum_engagement_score(valid) == dr.forum_engagement_score(polluted) == 150
    assert sorted([missing, valid], key=dr.forum_engagement_score, reverse=True) == [valid, missing]
