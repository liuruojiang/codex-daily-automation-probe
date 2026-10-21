# Codex Daily Automations

This public repository hosts cloud-scheduled digest workflows that run independently of the local Codex Desktop app.

Microcap v2.0 now requires revision `plain_mom16_fixed1_20260904`: 16-day relative momentum, zero exit buffer, overheat OFF, target volatility OFF, fixed one-times execution with a 0.8 hedge. The mandatory final CSV must carry this identity; the retired v2.0 target-vol identity is rejected. v2.3 and v2.5 identities are unchanged.

Current workflows include AI HOT, US ETF and asset-allocation, MNT advisory, and Microcap Top100 digests. The scheduled Microcap publication is close-confirmed and must never be relabelled as realtime. The workflow publishes a compact holdings-first email for v2.0, v2.3, and v2.5 while retaining momentum, hedge momentum, data freshness, and failure-gate checks. It checks out an immutable strategy commit, restores and validates a full-universe rebalance cache, refreshes one authoritative state bundle before all three isolated version runs, validates each exact strategy identity from a mandatory final CSV, separates dated list actions from historical/preview context, records the strategy SHA in the email, and preserves correction and duplicate-delivery gates.

IC/IM uses the v1.4-r1 post-close workflow at 20:00 Asia/Shanghai, with Cloudflare as the normal trigger and the GitHub schedule as fallback. It fixes the strategy execution commit to `68dcc8d978ae5dae4a60c5fbd4a5c8c3c2a2d300`, including the validated Tencent fallback, Put summary repairs and committed-plan opening-evidence guards, restores and validates the hash-chained `ic-im-v1-4-r1-ledger`, and keeps intent and completed-delivery markers separate. The fix11 identity applies to signal dates from 2026-10-08; historical signals retain their own identities. Local Codex reports and cloud mail are accepted separately. The prior sync and audit evidence remains in [the 2026-10-07 record](docs/ic-im-v1-4-sync-20261007.md); the two standalone digest-gate gaps were repaired in the 2026-10-09 release.

Microcap's three checkouts use `6d09f4c3ad47f536fd76cce0990a6665f300269e`, including final-stream rollback protection. Its previously approved October 12 whole-state seed remains byte-for-byte unchanged and compatible. The 21-file local cumulative gate and cloud strategy gate retain all recovery counterexamples. The independent audit and scheduling boundaries are recorded in [the October 13 release](docs/ic_im_microcap_adversarial_readiness_20261013.md).

The manual v1.4 delivery-readiness workflow resolves the same production pin and checks the official producer, digest, TLS, SMTP authentication and envelopes without DATA or production delivery uploads. Readiness is not proof of an actual send or inbox receipt. Historical v1.2 realtime workflows retain their separate versioned state and delivery namespace. The public strategy checkout needs no access token; Gmail continues to use the existing protected `MAIL_*` secrets. Never place credentials or private information in source, workflow logs, or public artifacts.

Run the regression suite with:

```powershell
python -m pytest -q
```

## ETF collection and delivery integrity

ETF builds freeze the publication cutoff at script start and retain `collection_manifest.json`
and `history_before.json` alongside the exact plain/HTML email metadata. The manifest records
bounded source observations, raw candidates and rendered items; a bounded or empty listing
does not establish complete source coverage. Only actually rendered items enter sent history,
which the workflow persists after successful Gmail delivery.

`python scripts/validate_etf_delivery.py artifacts/metadata.json` is a mandatory pre-send gate.
It rejects body-hash, rendered-link, HTML-link and history mismatches. A manual ETF workflow
with `send_email=false` exercises the same build/gate/artifact path without sending or persisting
history. Sending runs require a verified activation record and commit a durable
same-day delivery intent before SMTP. Missing activation, an existing intent or
an uncertain send blocks automatic delivery/retry. A later workflow failure does
not undo confirmed SMTP acceptance. Before deployment, stop/reconcile the old
sender, review legacy evidence and choose a future first guarded report date as
described in [ETF delivery and recovery](docs/ETF_DELIVERY_STATE.md). No activation
record or production cutover is supplied by this change.

The local 08:00 reader runs `scripts/etf_preflight.py` against the original run's artifacts,
GitHub step evidence and independently recovered source inventory. It preserves same-day
earlier sends. `FAILED` overrides `PARTIAL`, which overrides `PASS`; missing historical snapshots,
unresolved source coverage or unchecked aggregation children must not become a clean PASS.
The audit does not replace source reading or make production scoring independent evidence.
Automated Chinese text must not invent article facts from familiar titles or keywords; when
translation evidence is insufficient, the email labels the limitation and the local reader
performs source-checked interpretation.
