# IC/IM reviewed source deployment, 2026-11-12

Production strategy pin: `41aa5accfe9413d7ad0134589e95819d7a1401a2` (strategy PR #64).
Registered release: `20261112-expert-review-v5-r2`.

The complete release contains 31 execution modules and three support files: the approved calendar data, its captured source text and the dependency requirements. The formal 56-file strategy entry passed 1571 tests, zero failures/errors/skips and the original five deselections; 194 source, support, fixture and test bindings were identical before/after.

The producer main/adapter bytes remain the frozen expert-audit candidate. No strategy parameters, fix11 identities, historical outputs, production ledger or account operations change. The 2027 State Council derivation retains `exchange_notice_verified=false`, its approved signal-day boundary and both source hashes. Existing official calendar years retain precedence; other unknown years reject.

Cloud source verification selects the highest registered manifest present in the expected Git commit. Missing or changed working-tree bytes cannot cause a downgrade to a 29-module manifest. Both the normal daily packager and the no-send readiness packager explicitly require this registered release; old proof rendering without a requested current release retains its historical compatibility.

The cloud calendar consumer scopes the derivation to the payload's signal day, rechecks source bytes/metadata and restores request state on exceptions. Independent source review found no confirmed issue in this scope; passive future-expiry strings do not create a new calendar input requirement. A separate proof review identified the manifest-downgrade defect, whose counterexample is now rejected.

Local targeted proof checks passed 18 cases, with an additional packaging-entry check. Existing successful local checks are reused only under unchanged function/source closure. The deployment must also pass the real cloud regression before merge. The existing readiness workflow restores the latest successful production ledger to an isolated volume, generates and validates the current report, and checks SMTP envelopes without sending DATA. Its temporary ledger is not promoted as a production ledger.

Normal 20:00 scheduling, source/date/freshness rules, normal-send intent/receipt/marker and SMTP gates remain. This publication does not request another email or a production ledger rewrite. Local Git metadata synchronization is separately guarded by the strategy repository's `tools/sync_reviewed_release.ps1`; the deployment session cannot write that local index.
