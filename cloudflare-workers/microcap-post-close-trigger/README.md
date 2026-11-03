# Post-close Cloudflare trigger

This Worker dispatches daily workflows through GitHub's `workflow_dispatch` API.
All times below are Beijing / Asia/Shanghai (UTC+8):

| Time | Days | Workflow | Native GitHub fallback |
| --- | --- | --- | --- |
| 05:30 | Every day | ETF / asset allocation | 06:00 |
| 09:00 | Every day | AI HOT | 09:30 |
| 16:00 | Monday–Friday | Microcap, close-confirmed | Existing schedule |
| 20:00 | Monday–Friday | IC/IM v1.4, close-confirmed | Existing schedule |

The two research digests use `send_email: true`; the China-market digests retain
their publication-mode inputs. Cron expressions are UTC, so ETF's 21:30 UTC
trigger belongs to the following Beijing calendar day. Weekend ETF content rules
remain in the existing builder. Workflow concurrency and trusted daily delivery
evidence guard against duplicate mail when a fallback or retried dispatch fires.
An unknown send outcome blocks automatic retry; dispatch acceptance alone does
not prove SMTP acceptance or inbox delivery.

Deploy with the `GITHUB_TOKEN` Worker secret already set. The token needs only
Actions read/write access to `liuruojiang/codex-daily-automation-probe`.
