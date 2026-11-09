# Independent maintenance source repair

Nov9 run 42543177978 successfully restored the approved Nov5 whole state and
failed only at the Nov5 notice-boundary comparison. The raw failed artifact is
13643390852, SHA-256
`6ff2770d7e99cd4161af4c1a4d8a33c9a5ab2b0366145297fd8fc8fd7f5f71f6`.
The exact 3 late investor records and 5 category corrections are document-reviewed
in the strategy repository's `docs/maintenance_notice_boundary_20261109.md`.

Keep `microcap_approved_state.json` and the native restore identity unchanged.
After that restore succeeds, `activate_microcap_maintenance_runtime.py` verifies
the original receipt, clean source checkout, complete Git tree difference and
all exact source hashes in `microcap_maintenance_runtime.json`. Only the native
notice-boundary adapter and the paired dated-share verifier/source-compatibility
review may change; tests/docs may accompany them. An unrelated
execution change aborts before checkout. The original restore receipt and all
economic/source data remain intact. The separate activation receipt grants no
current-data freshness or historical migration.

The first real repaired run, 42548898079, passed the exact source switch and
notice boundary, then exposed a stale restored `current_st.csv`: it still
contained 300237/ST美晨 after the approved Nov6 withdrawal. The official issuer
announcement is `https://static.cninfo.com.cn/finalpage/2026-11-05/1225658367.PDF`.
A new native current-ST fetch on Nov9 returned 191 rows and excluded 300237;
an independent current quote called it 美晨科技. The maintenance invocation now
uses the existing `--force-refresh-static-inputs` option to refresh both current
universe and ST lists. Historical interval/evidence checks and the prohibition
on implicit full rebuild remain unchanged. This is current-source renewal,
not a historical rewrite or an exception to the expired-interval guard.

Both maintenance and actual rebalance use this verified runtime. Cache keys bind
its exact SHA, so an older runtime's maintenance cache cannot be selected.
The normal native refresh/check and rebalance gates are retained. Daily runtime,
checkpoints, mail transactions, Cloudflare schedules and sender are unchanged.

Software validation covers real Git activation and wrong hashes/commits,
unrelated code, dirty source, unsuccessful restoration and receipt overwrite.
Existing maintenance isolation, period-handoff, member-daily and bounded-retry
tests remain. A live native notice refresh advanced Nov5 to Nov6 in an isolated
root; this is component acceptance, not complete maintenance or daily delivery.

Run 42551243114 then passed notice and current-ST checks but failed because
002316's live quote was Nov9, while maintenance requested Nov6. The 09:00 task
retains its complete prior-session maintenance responsibility. No other daily
full-base updater replaces it; no schedule or scope change is made.

The reviewed native repair additionally collects exact-date historical raw
TOTAL_SHARES from Eastmoney's existing public daily valuation report, checks
both adjacent sessions against the old authenticated boundary and preserves
all original quote/dual-inventory/Sina/document/time/history guards. The original
later quote is retained as a later observation. Historical evidence is never
created by changing a quote timestamp. Actual 002316 observations, negative
tests and the narrowly bound Nov5 source-only compatibility review are in
`docs/maintenance_historical_share_close_20261109.md` in the strategy repository.
All 100 actual current members passed the historical-source precheck; the native
002316 full source chain passed renewal/readback without changing the old share
CSV or primary evidence. Complete maintenance still needs its real remote run.

The push/manual full delivery regression also checks out the exact maintenance runtime
and runs its new/old share and notice guards separately from the existing daily
runtime/test-source role. Family-specific daily calls do not acquire this
maintenance dependency. The daily pins remain unchanged. Multiple missing
sessions are renewed sequentially under one symbol lock; each native adjacent
proof is retained, and retries resume at the first missing session.

Run 42558613202 reached 301288 after the preceding repairs and rejected a
NOTICE_DATE Nov6 in the already-approved Nov5 zero-total event. The newly
observed entire row equals the sealed approved row exactly. The native repair
now qualifies that exact next-session reobservation against the original
approval, old candidate, exact full EM row and original Sina Nov5 row. New
events, changed rows/shares and other dates remain blocked. The earlier v1
source review is retained; a separate v2 review binds the exact successor to
the same unchanged Nov5 input identity.

The new runtime includes the portable document-text test correction directly.
The independent Linux test job uses that exact runtime without a test overlay.

Rollback: revert the workflow/config/activation change together. The original
baseline pin, approved seed and successful historical recovery artifact are
unchanged. A rollback would again block at the unreviewed catalog boundary;
it must never be represented as a successful current maintenance run.
