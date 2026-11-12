# IC/IM reviewed source deployment, 2026-11-12

Production strategy pin: `9ec8b91c5256155101faae3c84dba9ff95632db5` (strategy PRs #64 and #65).
Required release: `20261112-expert-review-v5-r3`.

The complete inventory contains 31 execution modules and three support files: approved calendar data, captured source text and dependencies. Original r2 audit outputs and manifests remain frozen. Actual Python 3.11 cloud testing exposed two compatibility failures after 1569 passing cases; r3 explicitly initializes the requests transport exception and fixes Pandas at verified version 2.3.3. Other execution AST, codec bytes, assertions, strategy parameters and risk rules remain unchanged.

The two original failure controls pass locally. Independent whole-module AST review verifies the narrow initializer change. The subsequent local full run executed all 1571 cases without assertion failures but exceeded the original 60-second watchdog and was rejected. Promotion requires the actual cloud complete gate: the same 56 files, original five deselections, zero failures/errors/skips and unchanged timeout. No local timeout result is presented as formal acceptance.

The approved 2027 calendar retains `exchange_notice_verified=false`, its signal-day boundary and both source hashes. Existing official calendar years retain precedence; unknown years reject. Calendar consumption remains scoped to the payload date and validates metadata and bytes.

Source verification chooses the highest registered manifest in the expected Git commit. Missing r3 working-tree manifests cannot downgrade to r2 or legacy inventories. Both production and readiness explicitly require r3; historical APIs still read valid older proofs without relabeling them. The affected 26 proof, compatibility and workflow checks pass with unchanged before/after bindings. Independent shared-workflow comparison preserves main's historical microcap fetch and changes only ICIM failure-log/artifact retention.

After actual cloud CI, the existing readiness procedure restores the most recent successful production ledger into an isolated directory, generates and validates the current report and marker, and checks SMTP authentication/envelopes without DATA. This temporary ledger is not promoted. Normal 20:00 scheduling, date/source/freshness checks and normal-send intent/receipt/marker conditions remain unchanged. This publication requests no extra mail or orders. Local Git metadata synchronization remains guarded by the strategy repository's `tools/sync_reviewed_release.ps1`; the deployment session cannot write its local index.
