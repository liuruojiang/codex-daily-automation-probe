# Codex Daily Automations

This public repository hosts cloud-scheduled digest workflows that run independently of the local Codex Desktop app.

Current workflows include AI HOT, US ETF and asset-allocation, MNT advisory, and Microcap Top100 digests. The scheduled Microcap publication is realtime; a manual after-close correction must explicitly use `publication_mode=close_confirmed` and must never be relabelled as realtime. The workflow publishes a compact holdings-first email for v2.0, v2.3, and v2.5 while retaining momentum, hedge momentum, data freshness, and failure-gate checks. It checks out an immutable strategy commit, validates each exact strategy identity from a mandatory final CSV, separates dated list actions from historical/preview context, records the strategy SHA in the email, and preserves correction and duplicate-delivery gates.

The IC/IM v1.2 realtime workflow follows the Microcap trigger times (13:03/13:18/13:33 Asia/Shanghai; recent GitHub delay usually delivers around 14:20-14:40). It restores a hash-chained ledger, catches both products up atomically through the latest completed session, and sends one provisional intraday Gmail from that common state. Intraday snapshots never mutate the close ledger; `close_confirmed` is available only as an explicit manual correction mode. The public strategy checkout needs no access token; Gmail continues to use the existing protected `MAIL_*` secrets. Never place credentials or private information in source, workflow logs, or public artifacts.

Run the regression suite with:

```powershell
python -m pytest -q
```
