# Step 12: automatic shadow-mail coverage and fallback

`scripts/etf_automatic_mail_preview.py` consumes a saved metadata/manifest/pre-build-history triple and an optional reviewed-draft registry. It automatically determines which of the already selected articles can use the strict step11 replacement path. Every selected article appears once in a coverage list. Uncovered, stale, invalid or rejected drafts retain their original article text with a visible reason in that list; retained text is explicitly not described as newly reviewed. No model request, email sending or history update occurs.

The existing `etf-shadow-digest.yml` draft now invokes this adapter after its offline batch plan. It preserves mail HTML and coverage JSON for30 days in a separate `etf-shadow-mail-preview` artifact, keeping the existing checkpoint artifact unchanged. This workflow is still unmerged/inactive on the default branch. The actual cloud acceptance run uses the existing draft-PR regression workflow, same-repository/specific-branch gated, with contents/actions read permissions and no model credentials.

## Reviewed registry

`config/etf-shadow-reviewed-registry.json` contains the two completed step11 cards for the fixed2026-10-20 production snapshot. Its envelope binds run ID, source head, cutoff, original email body hash and pre-build history hash. A change in any field invalidates reuse of the old registry. A different snapshot produces a complete zero-replacement preview, rather than updating old publication dates or carrying forward the old editor's approval.

The file contains Chinese claims, exact source sentence IDs, structured model decisions, per-claim editor records and date/scope notes. It contains no source paragraphs, English quotation fields, raw provider reasoning, API credentials or complete article bodies. Source quotes are reconstructed from the exact current capture, and the existing review/editor card hashes must still match. This compact representation was verified to expand exactly to both unchanged step11 cards. These two records are acceptance samples, not authority to edit other sources or a general daily cache. The registry is not an independent signing or approval service.

## Per-article decisions

Only one registry entry per canonical URL can be considered. Duplicate entries hold the whole URL rather than choosing a more favorable review. Stale material IDs, unknown sentence IDs, stale editor hashes, failed model/semantic checks, missing editing, date/history failures and missing/ambiguous original article blocks all preserve that article's original text and report a reason. A failed article does not prevent another eligible article from being previewed. Unselected registry entries never add articles to the email.

The step10 integrity validator checks the original report before any fallback decision. Each accepted entry is checked with the strict step11 adapter; the combined accepted set is checked again before final rendering. Corrupt original input, unsupported registry envelope or final composition failure is fatal, not disguised as a successful zero-coverage fallback. The CLI requires a new directory, and outputs only `automatic-mail-preview.html` and `automatic-mail-coverage.json`, never sender-ready metadata.

The page starts with coverage counts and a per-article reading/decision table, followed by the complete shadow email. It distinguishes replacement from retained/unreviewed text. The original22 article selection, original market data and FAILED source-warning disclosure (7 failures,119 gaps) remain unchanged. Source review still means source faithfulness only, with `delivery_eligible:false` and `sent:false` in every case.

## Acceptance cases

`scripts/etf_automatic_preview_probe.py` runs these cases on the saved real production collection. Fault cases deliberately modify copies of the registry; they are synthetic and are not recorded as actual model/provider failures. Model and network calls are explicitly forbidden during all rendering and replay.

| Case | Replaced | Retained | Expected reason |
| --- | ---: | ---: | --- |
| Normal reviewed registry | 2 | 20 |20 articles have no reviewed card |
| One invalid editor hash | 1 | 21 |1 invalid review record; other approved article still works |
| Registry bound to another body hash | 0 | 22 |2 stale snapshot entries;20 uncovered articles |
| One injected model rejection, editor still confirmed | 1 | 21 |1 incomplete/rejected review; editor cannot override it |
| No registry supplied | 0 | 22 |All22 articles uncovered; full original email remains |

Each case preserves the same article selection, original warning counts and input file bytes. Zero-replacement cases keep the original body hash exactly. Each report/page replays identically offline. The cloud acceptance job downloads the trusted saved production artifact and uploads the proof plus all five previews for30 days; it does not depend on files from the local machine.

Local verification:75 related tests and24 subtests passed, including mixed success/failure, snapshot changes, malformed compact claims, duplicate URLs, source mismatch, missing editing, model rejection, date refusal, unsafe/unselected entries, zero-coverage output, fatal integrity/composition errors, no sender metadata and workflow read-only/archive contracts. A real local probe passed all five scenarios. The normal user page contains2 replacements and20 explicit retained/unreviewed rows. No additional model calls were made.

Browser visual and real email-client compatibility remain unverified; static HTML/link/content checks are used. Existing limits on heuristics, truncated inputs, unverified external financial/tax claims, source completeness and fixed artifact expiry continue to apply. Production deployment/scheduling and actual mail delivery are outside this draft acceptance step. The next source collection needs its own matching reviewed records; the fixed registry cannot manufacture coverage for it.
