# IC/IM CFFEX transport and delivery contract release — 2026-10-09

## Scope

- Production strategy pin: `408c9badea5351ae7c0021f9b1e9adfdb804d554`, merged strategy PR [#41](https://github.com/liuruojiang/ic-im-rolling-arbitrage/pull/41).
- Strategy identity remains fix11: `v1.4-20261008-r1-ic-csi500-abs40-fix11`; historical signal identities and ledgers are unchanged.
- CFFEX interrupted-body retries, shared download deadline checks, response cleanup, and current-query failure diagnostics are covered by the published strategy tests.
- The independent packager now checks official covered exchange sessions, the next actual session, finite nonnegative quantities, sleeve totals, momentum scaling, and ordinary/3x Put pending-plan dates and lifecycle collisions. Marker CLI runs the same complete validation before writing.
- The production and readiness workflows compare the packager calendar with the pinned strategy calendar. Readiness uses the production ChinaBond source and the same explicitly disclosed fallback.

## Reproduction and acceptance

On 2026-10-09, the exact published strategy archive passed the workflow's 30-file suite: **531 passed, zero failures/errors/skips**, 13.46 seconds of pytest time (15.535 seconds including process/setup), with a 60-second subprocess limit and isolated `ICIM_STATE_DIR`, `ICIM_REQUIRE_MIGRATION=0`.

The automation IC/IM workflow family passed **192 tests and 12 subtests**, zero failures/errors/skips, 4.03 seconds of pytest time. The contract owner's focused suite passed 127 tests and 10 subtests; a separate reviewer passed 36 adversarial cases. These counts overlap and must not be added together.

The frozen real 10/08 ledger fixture has 11 journal records, migration proof and latest pointer. Its manifest preserves each original artifact SHA-256; `.gitattributes` disables JSON newline conversion. The workflow copies it only into a disposable regression checkout to exercise the real-history missing-pointer guard. Test process state still points to runner temporary storage. Production and readiness never restore this fixture.

Repacking the real 10/08 result preserved its subject, body and HTML byte for byte. The historical actual send was run [37773833605](https://github.com/liuruojiang/codex-daily-automation-probe/actions/runs/37773833605); its ledger is seq10 / 2026-10-08 / `8b6e07f348fa372668fe312f83830b8c2e766934dfcc26023b5cd44485b3d4bb`.

## Delivery boundary

The normal cloud schedule and durable intent/delivered markers are unchanged. Publishing this code and passing regression do not certify a new daily email. After merge, run `ic-im-v1-4-delivery-readiness.yml` on main and inspect its actual producer result, ledger digest, packaged metadata and no-send SMTP envelope receipt. Readiness must not issue SMTP DATA or upload a production ledger, send intent or delivered marker. Actual normal email delivery requires separate run and Gmail INBOX evidence.
