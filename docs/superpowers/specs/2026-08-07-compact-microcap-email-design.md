# Compact Microcap Email Design

## Goal

Replace the verbose Microcap Top100 realtime email body with a decision-first digest that makes the daily action, holdings, execution scale, momentum score, and active risk state visible at a glance.

## Scope

- Change only the microcap digest builder, its workflow arguments, and microcap-specific tests.
- Do not change any strategy code, signal calculation, refresh behavior, schedule, recipients, or other digest families.
- Keep raw stdout and generated realtime CSV files in the existing GitHub Actions artifact instead of copying them into the email body.

## Data contract

The builder continues to accept each version's stdout result and additionally accepts an optional version-qualified realtime signal CSV. Fields from the CSV supplement the stdout key/value lines so the email can display execution and risk state that is not printed by every version.

The three versions use different momentum labels:

- `v2.0`: microcap momentum, hedge momentum, and their plain momentum gap.
- `v2.3`: annualized log-WLS score of the hedged spread and its R-squared.
- `v2.5`: annualized log-WLS score of the unhedged microcap series and its R-squared.

The digest must not label the v2.3 or v2.5 legacy `momentum_gap` alias as a plain momentum gap.

## Email structure

The subject is one of:

- `[需操作] 微盘股 v2.0/v2.3/v2.5 日报 - YYYY-MM-DD`
- `[无需操作] 微盘股 v2.0/v2.3/v2.5 日报 - YYYY-MM-DD`
- `[异常] 微盘股 v2.0/v2.3/v2.5 日报 - YYYY-MM-DD`

`异常` takes precedence over action when any version is failed or stale. Otherwise `需操作` is used when any version changes holding direction or execution scale.

The body contains:

1. A one-sentence conclusion.
2. A compact three-row table: version, current-to-next holding, action, next-session scale, and version-correct momentum score.
3. A short attention section containing only active risk controls or data/script failures. When none exist, show `风险/数据异常：无`.
4. One data-time line and one GitHub Run link when available.

The body excludes raw stdout, internal paths, exit-code lists, cache plumbing, and the full scheduling audit.

## Risk wording

- v2.0 highlights an active `blocked_until_signal_reset` state and, when available, its current overheat metric and trigger threshold.
- v2.3 highlights `overheat_risk_off` and, when available, its current value, trigger threshold, and recovery threshold.
- Failed or stale versions display their status reason.
- v2.5 does not invent an overheat warning when its official stream has no such active overlay.

## Failure behavior

The email is still sent when a version fails or is stale. Its subject becomes `[异常]`, the affected version is marked unavailable in the table, and the concise failure reason is shown in the attention section. Missing optional CSV files do not suppress an otherwise valid stdout signal; the builder falls back to stdout fields.

## Verification

Tests cover no-action, actionable, stale/failed, version-specific momentum labels, active overheat states, CSV fallback, and the absence of raw diagnostic sections from the email body. The existing microcap workflow guard tests must remain green.
