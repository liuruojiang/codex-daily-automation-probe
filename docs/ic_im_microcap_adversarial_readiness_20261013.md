# IC/IM and Microcap adversarial delivery release — October 13

The user authorized independent adversarial review, direct repairs, production/cloud synchronization and scheduling verification. This release keeps all strategy parameters, approved historical lineage, exchange-session rules, publication cutoffs and delivery namespaces unchanged.

## Reproduced issues and repairs

| Boundary | Reproduced counterexample | Repair |
|---|---|---|
| Microcap whole restore | An old whole bundle could replace a newer final NAV/signal when base files were incomplete (12 cases) | Check all three versions' costed NAV/NAV/latest signal/performance NAV before any write and again under the lock; reject invalid local dates |
| IC/IM profit opening | Altered contracts, quantities or absent sources could be accepted by a new record | Bind both opening legs to the previous close's exact pending plan and execution session; retain actual producer timing |
| Ordinary Put opening | Missing quote source or conflicting duplicate contract evidence could be accepted | Require nonblank provenance and one quote per exact contract/security identity |
| External trigger | Permanent HTTP failures were caught as connection errors and retried; a failed response body lost its status | Classify HTTP status outside the connection catch; keep transient failures bounded to three attempts and retain status when a body fails |

## Immutable strategy and state identities

- Microcap production checkout: `6d09f4c3ad47f536fd76cce0990a6665f300269e` (PR 73).
- IC/IM production checkout: `68dcc8d978ae5dae4a60c5fbd4a5c8c3c2a2d300` (PR 45).
- The October 12 Microcap seed remains 71,254,288 bytes, SHA-256 `22cdff59543fb03263c6184031db52d728f985cc641b01efb518ea79f04e078d`, with 14,982 verified payload entries. Actual archive inputs/artifacts match the new code's unchanged model/authority/base inputs. The transport-script change is outside that payload. This is not a new history approval or a historical rebuild.
- IC/IM fix11 build/rule/delivery identities and all frozen historical records remain unchanged. Local and cloud seq12 ledgers are independent valid continuations, with common seq0–11 and identical projected state anchors. A non-descendant cloud chain must never replace canonical local ancestry.

## Measured local verification

- Microcap cumulative **21 files: 443 passed, zero failed/error/skipped, 50.133 seconds**, real 60-second process limit. All 23 new cases remain; only immutable test-bundle packing is shared between isolated destinations.
- IC/IM cumulative **24 files: 454 passed, zero failed/error/skipped, 11.726 seconds**, real 60-second process limit, isolated state and migration switch 0. The stable state-suite alias actually collects all 38 new cases; adding the underlying file again would duplicate collection.
- Independent read-only production hashes did not change. The Microcap six-leg authority, full metadata/cache fingerprint, 100 unique/ST0 members, actual empty-cache cold-seed route and six altered seed legs were verified.
- Actual IC/IM producer timing controls passed. They do not claim actual account execution or retrospective paper fills.

## Scheduling and current-data boundary

The official local calendar confirms October 13 and 14 as exchange sessions. The completed-session anchors at 14:15 are respectively October 12 and 13; next execution sessions are October 14 and 15. Dates are calculated, not saved as future fallback constants.

The local recurring task is ACTIVE at weekdays 14:15 Beijing. At 06:37 October 13, the read-only scheduler database reported nominal next run 14:15 and actual next run 14:17:32 (application jitter). October 14's actual timestamp has not yet been materialized. Cloudflare's live authenticated API confirmed weekday 16:00 Microcap and 20:00 IC/IM crons, an encrypted GitHub binding, disabled public/preview URLs and a current 100% deployment. Both current GitHub workflows are active; v1.2 is disabled. The delayed October 13 04:32 GitHub fallback actually skipped producer, SMTP and ledger uploads.

At 06:52 October 13, the formal Microcap preflight correctly rejected missing 100/100 same-day member quotes before the market opened. It did not rewrite proof, NAV, panels, metadata or the manifest. Today's proof must be produced by the normal session run from real current quotes; yesterday's pass cannot stand in for it. This expected data-availability gate is separate from code/state/scheduler readiness.

## Delivery acceptance boundary

All new counterexamples are retained in the reusable regression workflow; Worker changes now trigger CI and are exercised for either strategy family. The no-send readiness workflow retains production pin/calendar/state checks and SMTP envelope validation without DATA or formal state/intent/marker uploads. Microcap validation retains its separate artifact namespace and cannot publish mail or normal delivery state.

Cloud regression, deployed Worker readback and any no-send runtime evidence must be accepted from their actual run artifacts after this release. Future daily quotes, actual SMTP acceptance and Gmail inbox delivery are not certified in advance. Normal emails are not resent and uncertain intents are not deleted.

## Completed cloud acceptance on October 13

- PR 142 merged the two production strategy pins and the trigger repair. [Regression run 38319591438](https://github.com/liuruojiang/codex-daily-automation-probe/actions/runs/38319591438) independently recorded **788 automation, 527 Microcap and 623 IC/IM tests**, with zero failures, errors or skips; each strategy artifact's tested SHA matches the pins above. Cloud counts include broader reusable suites and the existing state-suite alias, so they are not local cumulative-gate counts.
- The repaired Worker was deployed at 07:01 Beijing. Authenticated service readback confirmed active version `9945a836-dcec-4b36-981c-ee553a532e98` at 100%, both existing weekday crons and the existing encrypted GitHub binding. The CLI upload version `2afbfd92-25ce-4647-b52b-38de7cbd7f91` was followed by Wrangler's trigger/settings deployment; the active version is taken from the service rather than inferred from CLI output. All **22 transport cases** passed against the module read back from the service, with isolated fetches and no real dispatch.
- [IC/IM no-send readiness 38319994390](https://github.com/liuruojiang/codex-daily-automation-probe/actions/runs/38319994390) completed at 07:03:37 Beijing. It restored and verified official seq12 / October 12 / digest `06f14b267ad55d307145cd47c273d359095eb49bbd466bd4980d06f2b7c47d32`, used the new pin, produced the fix11 close report with zero ledger advances, validated both legs and passed SMTP TLS/authentication/envelope checks. `message_sent=false`; only readiness and test artifacts were uploaded. Its 623 strategy and 227 automation tests had zero failures, errors or skips.
- The prior complete Microcap validation-only run `38270281330` remains the actual approved-lineage recovery/refresh/three-version whole-delivery evidence. This release changes transport guards rather than model, payload or authority bytes; the approved immutable seed was independently revalidated. A new premarket run cannot manufacture October 13 quotes or a same-day proof, so today's fresh proof and today's/tomorrow's real signal remain normal session-time gates.

Exact commands, timestamps, exit codes, JUnit files, deployed-module SHA and independent JSON readbacks are retained in the local approved automation audit at `runs/20261012/adversarial_review_2347`. Neither formal state nor a normal message was published by this audit.
