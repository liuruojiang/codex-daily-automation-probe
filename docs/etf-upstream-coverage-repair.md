# ETF upstream coverage repair — 2026-10-22

The October 22 reader found 20 independently confirmed omissions: 19 Seeking
Alpha articles and one ETF.com interview. Five Seeking Alpha records appeared
in the production snapshot, but four were removed by ranking before original
source retrieval. Fourteen further Seeking Alpha records did not enter the
snapshot. The configured source list had no ETF.com interview monitor.

## Changes

- ETF research and fixed-monitor feeds capture every entry returned by their
  RSS/Atom document instead of applying a five/eight-item presentation limit
  before freshness and history checks. Other feed callers keep their limits.
- Relevant research below the ranking threshold reaches original-source
  retrieval. Generic mentions of leverage or an inverse ETF are research
  subjects, not automatic exclusions. Existing promotional/single-stock
  exclusions remain. Evidence, timestamp and sent-history gates remain.
- RSS paragraph boundaries, longer evidence and Dublin Core publication dates
  survive extraction. Missing descriptions are not generated from titles.
- A bounded ETF.com conferences/interview monitor follows actual article
  links and requires article-level publication evidence. It does not use a
  crawl time or an old interview recording date as publication time.

## Real evidence and limits

An offline replay used the actual saved October 22 Seeking Alpha RSS bytes
(SHA256 `6cabdab19b7ccb67b9732bf307e5f75afae70609b4be4e45ff624d0322404991`).
The returned feed contains 20 entries. The old first-five capture found five
of the 19 confirmed, in-window, previously unsent articles. The new collector
finds all 19 and attempts enrichment for all 19. Under deliberately blocked
article access, all 19 remain visible in the independent review audit, zero
are promoted to readable articles, and coverage stays PARTIAL.

This RSS contains titles and dates but no article descriptions. The repair
cannot make a paywall or blocked HTTP response into read evidence. A separate
publisher-page fixture tests ETF.com link/date extraction; no full historical
ETF.com listing was archived for that replay, so it is not claimed as a
twentieth real listing recovery.

Public metadata for all 20 omissions is retained as a regression fixture;
test RSS XML and HTML are explicitly reconstructed. The already confirmed
20-item supplement queue is preserved. Mail delivery code, activation state,
historical artifacts and original task memory are outside this patch.
