# Post-close Cloudflare trigger

This Worker dispatches the close-confirmed daily workflows through GitHub's
`workflow_dispatch` API. At 16:00 Beijing it triggers Microcap; at 20:00 Beijing
it triggers the current IC/IM v1.4 workflow. GitHub delivery markers and the
workflow concurrency group prevent duplicate mail when a fallback run also fires.

Deploy with the `GITHUB_TOKEN` Worker secret already set. The token needs only
Actions read/write access to `liuruojiang/codex-daily-automation-probe`.
