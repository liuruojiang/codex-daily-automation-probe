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
