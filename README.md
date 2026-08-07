# Codex Daily Automations

This private repository hosts cloud-scheduled digest workflows that run independently of the local Codex Desktop app.

Current workflows include AI HOT, US ETF and asset-allocation, MNT advisory, and Microcap Top100 realtime digests. The Microcap workflow publishes a compact holdings-first email for v2.0, v2.3, and v2.5 while retaining momentum, hedge momentum, data freshness, and failure-gate checks. It checks out an immutable strategy commit, validates each exact strategy identity from a mandatory final CSV, separates dated list actions from historical/preview context, records the strategy SHA in the email, and preserves correction and duplicate-delivery gates.

Run the regression suite with:

```powershell
python -m pytest -q
```
