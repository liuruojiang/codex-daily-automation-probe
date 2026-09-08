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
Missing ledgers, capture-count inconsistencies, corrupt history dates, selected
invalid items, and unexplained priority omissions fail closed before Send Gmail.
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

The 08:00 preflight recomputes schema-2 audits from immutable artifacts. A cutoff
not verified against the real Build interval cannot establish an omission.
Legacy artifacts remain PARTIAL; current re-fetches do not prove past coverage.

## Verification and delivery boundary

Focused tests include real public arXiv abstracts, negative evidence fixtures,
independent omission counterexamples, and CLI failure exit codes. Real collection
acceptance uses the normal workflow with `send_email=false`: build, gate, artifact
upload; Send Gmail and history persistence must both be skipped. No actual email
delivery claim is made from this replay. The user's no-resend decision remains
in force. Historical catch-up registers live outside sent-article history.
