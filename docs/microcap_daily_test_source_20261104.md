# Separate Microcap runtime and test-source identities

The approved runtime, state recovery pin and whole-state author remain
`d7615a3e3383e02f2b1e0eedfadfcb499ae7317a`. The approved C3 ZIP is unchanged.
The separately approved complete test-source role is
`45c7e72893fcc95b23339f7e82c8feda47757c2c`.

`config/microcap_approved_state.json` declares both roles and the precise four
reviewed test/doc paths, including old/new raw Git blob SHA-256. The shared,
stdlib-only `scripts/verify_microcap_daily_test_source.py verify` reads immutable
Git objects from the official repository. It accepts an actual repository HEAD
equal to either declared role, verifies the exact complete diff and proves that
every other tracked object is identical. It does not claim that the test commit
authored the runtime bundle or replace production working-file checks.

The reusable delivery regression used by both pull-request CI and the formal
Microcap workflow keeps the production checkout at the runtime pin. It creates
a fresh test worktree at the test pin using a raw Git archive, installing every
root file and all `data`, `docs`, `scripts`, `tests`, `poe_bots`, `.github`, and
`config` dependencies, plus the complete versioned Git `outputs` fixture tree
retained by the original CI checkout (18 files, 12,020,191 bytes at these pins),
and the versioned `.microcap_index_cache` fixture tree (6 files, 171,654 bytes).
Local preparation instead authenticates its complete C3 output fixture. Historical research pytest
symlinks are compared as immutable Git objects, not executed or materialized.
The archive's raw bytes are verified against each Git object, and a complete
source inventory is rechecked after execution. Authenticated fixture files are
installed only in that disposable test tree.

The existing core/release groups are retained, with the three share-source
regression files explicitly collected in the release group. This includes the
new JSON protocol and post-verification byte-race counterexamples. The new
full-baseline hash-contract file is separately qualified with the complete C3
fixture by the local repair acceptance; this CI group does not claim to run the
whole strategy test library. Original group JUnit files are uploaded on failure
as well as the combined report on success. `tested-sha.txt` records the runtime;
`tested-test-source-sha.txt` and the verification receipts record the test role.

This changes no strategy, historical data, recovery identity, schedule, SMTP,
delivery marker, or trading permission. CI success is code/test acceptance;
local intraday timing, real state handoff, no-send workflow runs and mail
delivery retain their separate acceptance requirements.
