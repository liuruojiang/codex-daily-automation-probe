# Codex Daily Automations

This private repository hosts cloud-scheduled digest workflows that run independently of the local Codex Desktop app.

Current workflows include AI HOT, US ETF and asset-allocation, MNT advisory, and Microcap Top100 digests. The scheduled Microcap publication is realtime; a manual after-close correction must explicitly use `publication_mode=close_confirmed` and must never be relabelled as realtime. The workflow publishes a compact holdings-first email for v2.0, v2.3, and v2.5 while retaining momentum, hedge momentum, data freshness, and failure-gate checks. It checks out an immutable strategy commit, validates each exact strategy identity from a mandatory final CSV, separates dated list actions from historical/preview context, records the strategy SHA in the email, and preserves correction and duplicate-delivery gates.

The IC/IM v1.2 close-confirmed workflow restores a hash-chained ledger artifact, advances both products atomically after the close, retries delayed exchange data, and sends Gmail only from a verified common state. Its private strategy checkout requires the repository secret `ICIM_REPO_TOKEN`; Gmail continues to use the existing `MAIL_*` secrets. Never place either credential in source or workflow logs.

Run the regression suite with:

```powershell
python -m pytest -q
```
