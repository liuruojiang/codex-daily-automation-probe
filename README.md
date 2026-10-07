# Codex Daily Automations

This public repository hosts cloud-scheduled digest workflows that run independently of the local Codex Desktop app.

Microcap v2.0 now requires revision `plain_mom16_fixed1_20260904`: 16-day relative momentum, zero exit buffer, overheat OFF, target volatility OFF, fixed one-times execution with a 0.8 hedge. The mandatory final CSV must carry this identity; the retired v2.0 target-vol identity is rejected. v2.3 and v2.5 identities are unchanged.

Current workflows include AI HOT, US ETF and asset-allocation, MNT advisory, and Microcap Top100 digests. The scheduled Microcap publication is close-confirmed and must never be relabelled as realtime. The workflow publishes a compact holdings-first email for v2.0, v2.3, and v2.5 while retaining momentum, hedge momentum, data freshness, and failure-gate checks. It checks out an immutable strategy commit, restores and validates a full-universe rebalance cache, refreshes one authoritative state bundle before all three isolated version runs, validates each exact strategy identity from a mandatory final CSV, separates dated list actions from historical/preview context, records the strategy SHA in the email, and preserves correction and duplicate-delivery gates.

IC/IM currently uses the v1.4-r1 post-close workflow at 20:00 Asia/Shanghai, with Cloudflare as the normal trigger and the GitHub schedule as fallback. It fixes the strategy execution commit to `739071a201506dd869b9a156e490c3bafd95a334`, restores and validates the hash-chained `ic-im-v1-4-r1-ledger`, and keeps intent and completed-delivery markers separate. The fix11 identity applies to signal dates from 2026-10-08; the last completed 2026-09-30 signal retains fix9. Local Codex reports and cloud mail are accepted separately. See [the 2026-10-07 sync and audit record](docs/ic-im-v1-4-sync-20261007.md) for verification evidence and two remaining standalone digest-gate gaps.

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
history; scheduled and default manual runs retain normal delivery.

The local 08:00 reader runs `scripts/etf_preflight.py` against the original run's artifacts,
GitHub step evidence and independently recovered source inventory. It preserves same-day
earlier sends. `FAILED` overrides `PARTIAL`, which overrides `PASS`; missing historical snapshots,
unresolved source coverage or unchecked aggregation children must not become a clean PASS.
The audit does not replace source reading or make production scoring independent evidence.
Automated Chinese text must not invent article facts from familiar titles or keywords; when
translation evidence is insufficient, the email labels the limitation and the local reader
performs source-checked interpretation.
