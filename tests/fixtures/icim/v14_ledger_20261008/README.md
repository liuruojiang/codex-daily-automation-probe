# Frozen real research ledger fixture

Source: successful main IC/IM close-digest run 37773833605, artifact 11549730442.
Verified day 2026-10-08, sequence 10, schema 4/r1. The complete 11-record
hash chain, migration proof and prefix equality to the previously verified
09/30 chain were checked before freezing these bytes.

`fixture_provenance.json` records every business file's SHA-256, the source
run/artifact and the downloaded ZIP hash. This fixture is only for offline
regression of real-chain copying and missing-pointer protection. It is
copied into a disposable strategy test checkout; tests mutate only their
temporary copies. ICIM_STATE_DIR remains a separate isolated test directory.

This is not a production restore source, current market input, target for a
new signal, or an email-delivery marker. Production and readiness continue
to restore the newest eligible official artifact through the formal restore
script. No test fixture is referenced by production send steps.
