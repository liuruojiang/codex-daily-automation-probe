# Microcap Corrected Digest Design

## Goal

Repair the complete production path that generated the 2026-08-07 abnormal Microcap digest, surface constituent-list trades as actions, reduce the impact of delayed GitHub cron delivery, and support one explicit corrected resend without changing strategy parameters.

## Observed failures

1. The production workflow checked out `liuruojiang/microcap@main`, where v2.5 passed the native holding label `long_microcap_top100` into the v2.0 overlay signal-row builder. The shared normalizer rejected that label and v2.5 exited with code 1.
2. The v2.0 and v2.3 realtime CSVs both reported `member_rebalance_required=True`, `member_enter_count=7`, and `member_exit_count=7`, but the digest action formatter only considered position and scale transitions. The email therefore said `无操作` despite an executable member rebalance.
3. The single 09:30 schedule was created at 12:09 and sent at 12:18. GitHub cron provides no strict start-time guarantee.

## Design

### v2.5 compatibility

Add a narrow adapter in `microcap_top100_mom16_biweekly_live_v2_5.py`. A copied dataframe maps the native microcap-only holding label to the shared v2.0 label only while calling the legacy row builder. The resulting row is then restored to v2.5-native current/next holding labels and v2.5-native turnover, hedge ratio, and leverage fields. The strategy signal, thresholds, target volatility, and execution parameters remain unchanged.

### Digest action rendering

Keep position entry/exit as the primary action. When the position remains active, combine any scale action with `member_rebalance_label`; when the label is missing, derive a concise label from enter/exit counts. A member-only change must no longer render as `无操作`, and it must make the subject/action conclusion actionable.

### Schedule redundancy and delivery idempotency

Replace the single cron with three off-minute morning triggers. Serialize runs with one workflow concurrency group. Before expensive refresh work, scheduled runs query GitHub Actions for a non-expired delivery-marker artifact named for the Beijing trading date. If a marker exists, the queued redundant run exits without sending. After a successful Gmail step, upload the marker. A manual `correction=true` dispatch bypasses the marker and adds a `纠正版` subject prefix.

The marker uses GitHub's own API and artifact storage, so no new third-party service or secret is required. Redundant cron improves the chance of an earlier start but cannot provide a hard delivery-time SLA; that would require an external scheduler.

## Verification

- Reproduce the v2.5 label exception on current microcap `origin/main`, then pass the same test after the adapter.
- Reproduce the 7-in/7-out email as `无操作`, then verify it renders as a member rebalance and changes the action summary.
- Test marker-name/date parsing, correction bypass, and workflow wiring without live network calls.
- Merge both repositories, dispatch the production workflow with `correction=true`, inspect all three uploaded realtime CSVs and the generated Markdown, and confirm the corrected Gmail body.

## Rollback

Revert the two focused merge commits. The schedule can return to its single cron without affecting strategy calculations. No data migration or strategy-state conversion is introduced.
