# Daily strategy delivery: recurrence-prevention contract

This is a delivery-control change, not a strategy, risk, fee, execution-timing or
account-order change. Microcap and ICIM retain their separate failure domains.

## Required before every daily delivery

Both production workflows call `delivery-regression.yml` at the same automation
revision. Their send jobs depend on successful regression jobs. The Microcap job
also requires a successful calendar lookup; a lookup exception is NOT a confirmed
holiday. The `always()` expression cannot bypass either prerequisite.

The reusable workflow uses read-only permissions and no mail secrets. It tests
the exact immutable strategy SHA extracted from each production workflow, and
retains JUnit results and the actual tested SHA. It never restores the production
ledger, runs market signals, sends mail or copies historical test data into a
production job. Daily callers select only their own strategy family. Push/PR
checks run both families plus the complete automation test suite.

## Known faults and permanent checks

| Failure class | Required control | Regression evidence |
|---|---|---|
| Yesterday's proof reused today | Independent day/member/ST preflight; old proof rejected | `test_realtime_preflight.py` |
| Partial or mismatched version outputs | Whole manifest, core/authority/input fingerprints, final CSV/date/identity checks | `test_top100_delivery.py` |
| Missing/altered cold-start or next-day cache | Hash-checked staged restore, locks, anti-rollback, backups, no auto-proof | `test_top100_cloud_delivery.py`, `test_full_rebalance_cache_bundle.py` |
| First cold generation presented as audited | Second formal generation required to yield clean rewrite audit | `test_microcap_workflow_refresh_gate.py` |
| Stale primary feed suppresses fallback | Every source validated before selection; all-invalid fails closed | `test_ohlcv_provider_validation.py`, including Hypothesis combinations |
| Transient disconnect or partial IC/IM signal | Bounded per-product retry; incomplete result cannot publish | `test_delivery_transport_retry.py`, `test_run_ic_im_v1_3_github_digest.py` |
| Corrupt/missing ledger or intraday mutation | Migration and persistent hash-chain invariants | `test_poe_ic_im_v1_3_state.py` |
| Failure email counted as normal delivery | Actual status, final CSV and delivery marker gates | Digest and workflow tests |
| Calendar source unavailable but run appears healthy | Raise failure, not holiday; manual caller cannot bypass failed calendar job | `test_delivery_regression_contract.py` executes actual embedded calendar code with fault injection |
| Tests work only on one laptop | Versioned real stale-OHLCV fixture with checksum, no silent test skipping | `tests/fixtures/icim/README.md` |

## Acceptance and release

1. Run relevant tests locally and preserve dirty production/research state.
2. Require green PR regression on the proposed commit, then merge.
3. Verify main's actual workflow runs, tested strategy SHA and JUnit artifacts.
4. For a fresh signal release, require final dated artifacts, normal email and
   marker success. A successful unit-test job is not market-data acceptance.
5. If the same date already has a normal delivery marker, an acceptance dispatch
   must skip signal/send rather than create another mail. Do not set correction
   just to obtain a green screenshot.

The local Codex task additionally runs bounded (60-second) delivery-only tests
per strategy before production state work, with an isolated test state directory.
It does not run the full repository suite, rebuild historical data or install
dependencies in the intraday window. Failure blocks only the affected family.
Known holidays skip first; the 14:30 local / 18:00 GitHub schedules remain unchanged.

These controls prevent covered software/state faults from silently re-entering
delivery. They do not guarantee upstream market availability, scheduler uptime,
or inbox receipt. New failure classes require a reproducible test and a verified
narrow repair; do not relax data integrity to make a test or workflow green.

Rollback: restore reviewed workflow files from the pre-change backup or revert
this change through a reviewed PR. Do not delete guards merely to release a
blocked signal. No mainline/frozen strategy data is changed by this contract.
