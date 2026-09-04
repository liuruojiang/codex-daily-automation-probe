# Codex Daily Automations

This public repository hosts cloud-scheduled digest workflows that run independently of the local Codex Desktop app.

Microcap v2.0 now requires revision `plain_mom16_fixed1_20260904`: 16-day relative momentum, zero exit buffer, overheat OFF, target volatility OFF, fixed one-times execution with a 0.8 hedge. The mandatory final CSV must carry this identity; the retired v2.0 target-vol identity is rejected. v2.3 and v2.5 identities are unchanged.

Current workflows include AI HOT, US ETF and asset-allocation, MNT advisory, and Microcap Top100 digests. The scheduled Microcap publication is close-confirmed and must never be relabelled as realtime. The workflow publishes a compact holdings-first email for v2.0, v2.3, and v2.5 while retaining momentum, hedge momentum, data freshness, and failure-gate checks. It checks out an immutable strategy commit, restores and validates a full-universe rebalance cache, refreshes one authoritative state bundle before all three isolated version runs, validates each exact strategy identity from a mandatory final CSV, separates dated list actions from historical/preview context, records the strategy SHA in the email, and preserves correction and duplicate-delivery gates.

The IC/IM v1.2 realtime workflow follows the Microcap trigger times (13:03/13:18/13:33 Asia/Shanghai; recent GitHub delay usually delivers around 14:20-14:40). It restores a hash-chained ledger, catches both products up atomically through the latest completed session, and sends one provisional intraday Gmail from that common state. Each IC/IM card shows current-to-target legs plus audited reasons for momentum, valuation grid, Put, Call, and roll status. Intraday snapshots never mutate the close ledger; `close_confirmed` is available only as an explicit manual correction mode. The public strategy checkout needs no access token; Gmail continues to use the existing protected `MAIL_*` secrets. Never place credentials or private information in source, workflow logs, or public artifacts.

Run the regression suite with:

```powershell
python -m pytest -q
```
