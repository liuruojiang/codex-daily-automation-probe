# Microcap post-close Cloudflare trigger

This Worker dispatches the existing GitHub Actions workflow in
`close_confirmed` mode. The workflow keeps the GitHub schedule as a fallback and
uses its delivery marker and concurrency group to prevent duplicate email.

Cloudflare dashboard configuration:

- Secret: `GITHUB_TOKEN`
- Cron Trigger: `0 10 * * MON-FRI` (10:00 UTC = 18:00 Asia/Shanghai)
- Worker source: `worker.js`

The fine-grained GitHub token must be restricted to
`liuruojiang/codex-daily-automation-probe` and have repository permission
`Actions: Read and write`.
