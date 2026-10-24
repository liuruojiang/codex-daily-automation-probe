# ETF historical replay and live provenance

The PR regression jobs replay three reviewed, immutable ZIPs from
`tests/fixtures/etf_archived`. ZIPs are base64 text so their original byte hashes
survive checkout newline conversion. The archived public metadata records what
GitHub reported at capture time; it is not a statement of current artifact
availability or live delivery authorization. No credentials are stored.

`scripts/etf_archived_source.py` verifies the exact ZIP digest, artifact identity,
producer repository, run, commit, workflow and applicable branch/event rules
before extraction. Checkpoint replay also checks the original checkout parents
and a different consumer run. Existing checkpoint inventory validation remains
mandatory. Proof output always says `archived_historical_replay`,
`current_live_status=not_checked`, and `delivery_eligible=false`.

The strict online `etf_fresh_shadow.py verify-source` and
`etf_cloud_checkpoint_probe.py verify-source / verify-resume` commands remain
available for fresh provenance acceptance. They do not fall back to archives on
HTTP failures, expired artifacts, wrong producers, or digest changes. Historical
regressions no longer depend on the 30-day Actions retention period.

To add another accepted baseline, first verify the live producer and immutable
artifact metadata, download and hash the exact ZIP, and retain a public-only
metadata capture. Review the new ZIP digest and identity pins together with its
expected results and model-usage provenance. Add a new named fixture and tests;
do not silently replace a prior baseline. Run the new fixture offline and the
strict live checks separately before using it for a fresh acceptance claim.
