# ETF delivery and recovery

SMTP acceptance and the overall Actions conclusion are separate facts. A trusted
`Send Gmail` step with conclusion `success`, or a source-bound `smtp_accepted`
receipt, confirms dispatch even when later history persistence fails. Keep the
original workflow conclusion and report `post_send_failure`; do not claim that
the message was unsent. SMTP acceptance does not prove inbox delivery or reading.

`scripts/etf_delivery_state.py` classifies independently fetched run/jobs evidence.
Its output does not replace producer identity, artifact integrity, report date or
body/subject verification. `etf_preflight.py` retains those checks. An embedded
`runproof.delivery_receipt` must come from the same trusted run artifact and bind
the run ID, head SHA, workflow path, body hash and subject hash. A plain copied JSON
object is not independently authenticated by this helper.

## Guarded production sending

The production workflow invokes `send_report.py --receipt ... --durable-github-etf`.
It exclusively creates and fsyncs a local intent, verifies the approved activation
record, then atomically creates a GitHub Contents checkpoint on the default branch
**before** contacting SMTP. The key covers repository, report date and normalized
destination group. A new run, new runner or changed body does not bypass that key.
Concurrent creates conflict; unconfirmed API writes never permit SMTP.

All existing checkpoints block automatic dispatch, including `in_flight`,
`delivery_unknown` and `smtp_accepted`. There is no force-resend switch. Stable
Message-ID values correlate evidence; SMTP does not promise deduplication by ID.
If a process dies, the SMTP reply is lost, a receipt cannot be saved or the remote
completion update fails, retain the checkpoint and reconcile against trusted
mail-provider evidence. Do not delete a checkpoint or retry on the assumption
that a workflow failure means no mail was sent. This deliberately favors avoiding
duplicates over availability: a pre-SMTP crash can require human reconciliation.

An accepted receipt remains in the always-uploaded artifacts even if a later
remote completion/history step fails. If artifact upload also fails, classify the
send from available trusted evidence, otherwise `delivery_unknown`; do not infer
non-delivery. The separate GitHub checkpoint still blocks another run. The
history workflow pulls with rebase before pushing, preserving committed ledger
files. The ledger must not be manually removed, rewritten or pruned as routine
history cleanup.

The generic sender without `--receipt` retains its existing behavior for other
strategies. `--receipt` alone guards only that local directory. Cross-runner ETF
protection requires the production workflow's durable mode. Repository writers
can change the code or ledger; this protocol does not protect against a malicious
administrator of its trusted repository.

## Deployment cutover (required; not performed by this change)

No activation record is automatically created. A missing, draft or mismatched
record blocks production SMTP. Do not enable the new workflow before completing
the following review in the trusted repository:

1. Stop the old unguarded sender and finish/reconcile all in-flight old runs.
2. Review legacy send evidence through the calendar day immediately preceding
   the first guarded report. Record actual run/receipt evidence and all unknowns;
   an unresolved legacy send cannot simply be classified as unsent.
3. Choose a first report date strictly after the UTC date of that review, at the
   future deployment. Commit `digest_history/etf_delivery/activation.json` on the
   default branch using the schema below, replacing every placeholder. Do not
   backdate this record to replay historical reports. The sender verifies the
   record each time and rejects reports before `first_report_date`.
4. Confirm a collection-only run and test the production environment's contents
   write permission and branch-protection compatibility without sending mail.
   Enable the guarded sender only after the cutover evidence is reviewable.

```json
{
  "schema_version": "etf-delivery-activation-v1",
  "state": "approved",
  "repository": "OWNER/REPOSITORY",
  "branch": "DEFAULT_BRANCH",
  "workflow_path": ".github/workflows/etf-allocation-digest.yml",
  "first_report_date": "FUTURE-YYYY-MM-DD",
  "legacy_coverage_through": "PREVIOUS-YYYY-MM-DD",
  "reviewed_at": "ACTUAL-ISO8601-WITH-TIMEZONE",
  "legacy_sender_stopped": true,
  "legacy_evidence": "Trusted run/receipt references and reconciliation result"
}
```

Authenticated GitHub metadata requests use a fixed `https://api.github.com`
origin and reject all redirects. Artifact download and source-pin checks remain
separate and unchanged. No real email, task activation, deployment or history
migration is performed by the offline regression suite.
