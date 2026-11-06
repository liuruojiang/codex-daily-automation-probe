# Member daily delivery

The daily route is `.github/workflows/microcap-member-daily.yml`. It reads the
exact runtime in `config/microcap_member_runtime.json`, restores only a small
checkpoint, fetches the effective members and hedge, computes the three native
signals, publishes a validated close checkpoint, renders, then sends. It has no
whole-state restore, all-market refresh, regression, or maintenance dependency.
Same-day confirmed checkpoints are rendered without fetching prices again.

`config/microcap_member_checkpoint.json` must contain the exact reviewed small
seed (`schema=member_daily_seed_v1`, `strategy_sha`, official release `url`,
`sha256`). It is used only when no previously published member checkpoint is
available. There is no whole-state fallback. The checkpoint producer artifact
name is bound to the helper runtime; the checkpoint itself retains its actual
economic source identity. These are different fields and must not be relabeled.

The original normal date/mode marker remains the complete report transaction.
A partial information report uses the suffix `-partial-information`, including
its own intent and SMTP receipt. It can be sent once and does not occupy the
complete report marker. An uncertain intent always requires reconciliation;
neither path deletes or bypasses it. Both workflows share the original daily
concurrency group, preventing an old/new sender race.

The explicit manual saved-report recovery inputs accept only an archive exactly
listed in `config/microcap_member_reports.json` (`schema=
member_daily_reviewed_reports_v1`, `reports[YYYY-MM-DD]={url,sha256,strategy_sha}`).
Its `manifest.json` must bind every source/report/checkpoint file, exact helper
runtime and market date. It restores already computed results and renders only.

The independent maintenance workflow receives `task=maintenance` at 09:00 and
`task=rebalance` at 15:10 Beijing. Both check the original exchange calendar;
rebalance skips ordinary trading days. Maintenance currently retains the native
legacy incremental workflow and may be expensive. It cannot block daily jobs.
It uses a separate legacy checkout/pin and exports reviewed native results via
the new helper. It does not send mail. Rebalance is published only after native
refresh/check and the original close/next-session handoff are verified.

Cloudflare dispatch switches only with `MEMBER_DAILY_ENABLED=true`. Without that
deployment flag, the existing 16:00 Microcap route remains active and the new
09:00/15:10 entries dispatch nothing. IC/IM 20:00 and other existing routes are
unchanged. Enable only after the exact runtime/seed and no-send daily acceptance
are verified. There must be one Microcap 16:00 sender, not both workflows.

Local IC/IM uses `run_icim_local_independent.py gate` and `run` with the existing
authenticated IC/IM snapshot. Its original 47-file native suite and independent
five-file suite, source pin, isolated test state and migration-required research
runner remain. It does not authorize through a Microcap resident or consumer.
An unsuccessful runner's current diagnostic can be delivered as partial
information without fabricating a complete signal or modifying the ledger.
