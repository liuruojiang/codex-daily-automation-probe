# ETF candidate omission guard

## Contract

The ETF build captures all items returned by each configured source before
selection. `collection_manifest.json` schema 2 preserves the original candidate
summary, source ID, publication date, any attempted enrichment, and a production
`pipeline_decision`. The latter explains the pipeline; it is not an independent
eligibility verdict. Research and fixed blog/podcast outputs no longer silently
stop at a fixed article count. Explicit caller limits remain available in the
selection API; the scheduled build asks for all qualifying research candidates.

`validate_etf_delivery.py` requires the manifest and run-bound
`history_before.json`. It validates the exact text/HTML/selection/history link
sets, then independently recomputes `etf_candidate_audit.audit_candidates`.
Missing ledgers, capture-count inconsistencies, and selected
invalid items fail closed before Send Gmail. From 2026-09-10, unexplained priority
omissions, selected-evidence review and unknown historical dates are content
advisories: preserve FAILED/PARTIAL audit results, disclose the exact status in
both email bodies and link affected candidates, then continue sending. Delivery
validation reports PASS_WITH_WARNINGS and records the findings in the Actions
summary. Hash/HTML/history consistency and invalid rendered dates remain blocking.
The email starts with a visible same-day collection alert; audit-only candidates
do not enter sent-article history. This changes delivery policy, not audit truth.
Production scores, `independent_eligible=false`, an arbitrary capacity reason,
and a stored PASS do not excuse contradictory evidence.

Relevance and readable evidence are separate. An abstract can support only an
abstract-level note, not a claim to have read the full paper. The production
evidence gate checks distinct substantive prose, not familiar financial keywords
or a title whitelist. The independent audit uses its own conservative prose and
topic checks, not the production scoring functions.

## Remaining uncertainty is visible

PARTIAL is allowed only with an explicit disclosure in the email. A readable
bounded feed is not proof of whole-site coverage; empty results do not establish
no updates. Unresolved candidates appear in a separate review section and in
`candidate_audit.json`. Review URLs are not formal `- 链接：` item blocks, are not
added to sent-article history, and do not expand the 08:00 deep-summary boundary.
Aggregator child coverage stays unresolved until independently established.
This is a rule-based guard, not an exhaustive semantic or historical coverage
guarantee. Source login/paywall failures remain evidence gaps, not invented text.

The independent window is 36 hours for primary research/fixed-source candidates;
research's existing 14-day backfill and community backfill up to 14 days remain
separate. Publication after the frozen build cutoff is never eligible. Dedup
uses 365-day pre-build history, preserving earlier same-day deliveries, and
same-run original-title duplicates map to an actual selected representative.

When a research feed and a fixed monitor capture the same selected publication
(canonical URL, publisher, normalized title and publication timestamp all match),
the fixed capture points to the independently audited research representative.
This preserves the research backfill window without treating its duplicate
capture as a new fixed-section update. Fixed-only articles still require 36-hour
freshness; conflicting identities and ordinary articles older than the research
backfill limit remain audit failures or gaps, not silent selections. Missing research evidence is disclosed for
review. Independently confirmed carryover follows the separate rule below.

## Confirmed omission carryover

The user authorized carrying confirmed omissions into a later scheduled email,
without resending the earlier whole email. A curator adds only independently
verified omissions to `digest_history/etf_confirmed_omissions.json`, including
the original run/date, publisher URL, publication time, evidence level, a
source-faithful Chinese note, and what remains to verify. The build renders
these in `前期确认漏项补送`, clearly apart from today's new research and the
unconfirmed audit list. Public publisher summaries are labelled as summaries;
they do not imply access to a paywalled full article.

The queue is a separately audited configured source. It uses the frozen
pre-build 365-day sent history to suppress already delivered URLs/titles. Only
visible carryover item blocks enter the selection manifest and sent history;
the workflow persists that history after Send Gmail succeeds. If a regular
feed still captures the same URL with a thin excerpt, the verified carryover
evidence represents that URL and the duplicate capture is retained for audit.
The confirmed carryover is allowed beyond the normal research backfill window,
but unverified old articles are not. The queue file remains as provenance;
successfully sent entries are suppressed by history instead of deleted.

The 08:00 preflight recomputes schema-2 audits from immutable artifacts. A cutoff
not verified against the real Build interval cannot establish an omission.
Legacy artifacts remain PARTIAL; current re-fetches do not prove past coverage.

## Verification and delivery boundary

Focused tests include real public arXiv abstracts, negative evidence fixtures,
independent omission counterexamples, and CLI failure exit codes. Real collection
acceptance uses the normal workflow with `send_email=false`: build, gate, artifact
upload; Send Gmail and history persistence must both be skipped. No actual email
delivery claim is made from this replay. Earlier whole emails are not resent;
confirmed omissions may appear once in a later normal email. Historical
catch-up registers live outside sent-article history until a carryover item is
actually rendered and sent.
