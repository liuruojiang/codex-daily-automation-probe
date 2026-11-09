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
both exact source hashes in `microcap_maintenance_runtime.json`. Only the native
notice-boundary adapter may change; tests/docs may accompany it. An unrelated
execution change aborts before checkout. The original restore receipt and all
economic/source data remain intact. The separate activation receipt grants no
current-data freshness or historical migration.

Both maintenance and actual rebalance use this verified runtime. Cache keys bind
its exact SHA, so an older runtime's maintenance cache cannot be selected.
The normal native refresh/check and rebalance gates are retained. Daily runtime,
checkpoints, mail transactions, Cloudflare schedules and sender are unchanged.

Software validation covers real Git activation and wrong hashes/commits,
unrelated code, dirty source, unsuccessful restoration and receipt overwrite.
Existing maintenance isolation, period-handoff, member-daily and bounded-retry
tests remain. A live native notice refresh advanced Nov5 to Nov6 in an isolated
root; this is component acceptance, not complete maintenance or daily delivery.

Rollback: revert the workflow/config/activation change together. The original
baseline pin, approved seed and successful historical recovery artifact are
unchanged. A rollback would again block at the unreviewed catalog boundary;
it must never be represented as a successful current maintenance run.
