# Daily delivery adversarial acceptance — 2026-09-04

Three isolated reviewers attacked Microcap, IC/IM, and the shared email/trigger
path. Root reviewed the patches and actual workflow composition. No trading
interface was called, no normal email was resent for testing, and frozen
strategy/authority inputs were not changed.

## Repairs and executable contracts

- IC/IM rejects nonboolean close confirmation and nonfinite/negative exposure
  before ledger commit; wrong time/date/budget is rejected before coordinator
  initialization. Original September 4 ledger fixtures retain exact anchor parity.
- Microcap rejects future anchors, conflicting same-session restore, Windows ZIP
  aliases, malformed actionable member dates, and interrupted partial completion.
  Remote release verification fetches missing immutable objects without checkout.
  Long backup paths are supported. Transport accepts an exact expected session;
  preflight independently confirms the supplied formal calendar date.
- SMTP partial recipient refusal is failure, not success. Normal send requires a
  durable mode/date-specific send-intent artifact first. If intent exists without
  completion, the next run blocks for Gmail reconciliation and explicit correction.
  This is conservative ambiguity handling, not SMTP exactly-once delivery.
- Microcap resolves publication mode before dedupe. Legacy markers are accepted
  only with the same run's valid date/mode/status metadata. IC marker lookup is
  paginated. Ledger restore verifies successful main workflow provenance and checks
  all ZIP members/required files/CRC before extraction.
- Missing completion marker files are hard errors. Both production workflows must
  pass the shared regression and pinned strategy tests before their delivery job.
  Both family suites include the shared adversarial tests.

## Local Codex isolation

Local offline IC tests use a unique temporary ICIM_STATE_DIR and explicitly set
ICIM_REQUIRE_MIGRATION=0 only in the test child. Production remains migration-gated
with value 1. The local 14:30 schedule and GitHub 18:00 schedule are unchanged.
Late local runs after 14:47 do not fabricate a missed intraday snapshot. One failed
strategy cannot suppress the other successful result.

## Boundaries retained

- The frozen Microcap core intentionally imposes a five-calendar-day history cap.
  Transport/preflight success after a long holiday does not override that policy;
  the first realtime session may still be BLOCKED pending an approved policy change.
- External market providers, SMTP, and schedulers can still fail. Uncertain data
  must not be shown as today's valid signal; uncertain sending requires reconciliation.
- Cloudflare fetch now has a 30-second timeout and three isolated Node fault cases,
  but repository changes alone are not proof of Worker deployment.
- The deployed workflow's final conclusion and artifacts must be checked separately
  from these local test results; acceptance run IDs are recorded in the run report.
