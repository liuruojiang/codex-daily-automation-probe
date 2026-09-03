# Microcap and IC/IM post-close Cloudflare trigger

This Worker dispatches the existing microcap and IC/IM GitHub Actions workflows
in `close_confirmed` mode. Both workflows keep their GitHub schedules as
fallbacks and use delivery markers and concurrency groups to prevent duplicate
email.

Cloudflare dashboard configuration:

- Secret: `GITHUB_TOKEN`
- Cron Trigger: `0 10 * * MON-FRI` (10:00 UTC = 18:00 Asia/Shanghai)
- Worker source: `worker.js`

The fine-grained GitHub token only needs access to
`liuruojiang/codex-daily-automation-probe`, with repository permission
`Actions: Read and write`. The strategy repositories do not need to be included
in this trigger token.

Merging this directory does not deploy the Worker. Production is complete only
after `wrangler deploy` succeeds against the intended Cloudflare account, the
`GITHUB_TOKEN` secret is present on the deployed Worker, the Cron Trigger is
visible in Cloudflare, and an 18:00 Beijing dispatch appears in GitHub Actions as
`workflow_dispatch` with the external-schedule guard path. A native GitHub
`schedule` run proves only that the fallback fired; it is not Worker deployment
evidence.
