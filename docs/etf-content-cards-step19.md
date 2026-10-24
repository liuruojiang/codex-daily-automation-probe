# Step19: explicit complete-text sections of the futures security master article

The fixed Handelsmeisterei source had206 captured blocks/45,453 characters and an81,216-character generation payload. It remains an oversized captured excerpt, not a complete-article certification. This step explicitly selects two entire top-level sections of its captured visible text, including every captured subheading and paragraph between their originalh2 boundaries. There is no character-limit truncation or automatic choice of passages.

| Section | Parent block range | Next top-level heading | Selected blocks |
| --- | --- | --- | ---: |
| Data sources |p20–p52 |p53 Building the universe |33 |
| Process automation |p158–p185 |p186 Conclusion |28 |

Together these are61 blocks/11,388 characters, producing a21,034-character planning payload. The original title, URL, host and2026-10-16T09:16:44+00:00 publication field are preserved. The new material ID ise607ffbb35892fba1d705ed79e2e8ec6c10e2983714e7c2af11dbd497a14a96c; the full captured parent is4c173865ecaa1fd4ad09d408d7db2062e13143cfd43c5e9d9086b9be3c249894. A new-to-parent paragraph map preserves exact origin across the gap between chapters.

The editor's introduction, universe-building, classification, conclusion, appendices and footnotes are outside this selection. Tables and figures were not extracted in the original capture and are not claimed complete here. The LME example belongs to an explicitly unselected chapter, not an unexplained loss. The conclusion's reported Sharpe improvement is not imported into this card. The source's still-oversized full capture and all historical flags remain unchanged.

## Source review and substantive editing

DeepSeek generated an initial card; Qwen passed it. Assistant editing nevertheless withheld it. It misspelled Interactive Brokers, asked whether the already-defined excerpt was full text, treated a known excluded LME example as a missing-context question, and asked about update frequency/triggers even though the chosen automation section explicitly explains annual review and daily/ad-hoc triggers. Its scattered facts omitted the main workflow.

An explicit revision, not an unchanged resample, corrects the name and changes the title to chapter selection. It adds the division between daily-price signals and bid/ask spread/slippage evidence, catalogue-versus-account permission, manual resolution of matching gaps and recorded inclusion/exclusion decisions, rebuild/check/failure-investigation steps and cost-inclusive recalibration. It retains the source's futures-versus-stock/ETF scope, delayed quote limitation and omitted small-contract volume/open-interest when larger-contract histories are used.

Questions now request future implementation/version/quality-control reproduction and inspection of the referenced workbook/code. Those files were not downloaded or verified. The source does provide a workbook link, whose target is absent from the plain-text capture; the new model's explanation that the source does not provide files is too absolute. The editor records that distinction and does not copy it into the card. Likewise, a checklist in an article does not establish that its actual code enforces a failed-check stop. The future experiment explicitly leaves that unverified.

The changed card passed a second complete Qwen review and all10 assistant claim checks. Three editorial issue dispositions are complete. The initial model pass, editor veto, original card, deterministic field changes and new complete review remain separately reconstructible. No global review prompt, number guard or source gate was relaxed.

Actual API use was1 generation plus2 complete reviews,3 requests,8,800 reserved output tokens and22,327 provider-reported total tokens, with0 unknown results/missing usage. The protocol allowed the initial2 requests plus at most1 changed-card review; that allowance was used once. Output reservation is not a verified dollar cap, and review completion is not external validation of broker rules, data feeds, costs, code or strategy performance.

## Selection integrity and cloud proof

`scripts/etf_section_research.py` first rebuilds the original full capture with its existing exact source selectors. It requires matching original capture identity/date/body metadata, including the old full-input size flag. It then maps actual originalh2 headings uniquely to the parent text, retains source order, and selects every captured paragraph to the next top-level boundary. Unknown, repeated, reordered or ambiguous headings fail. A selected chapter still exceeding the input limit fails rather than being clipped.

New record`config/etf-futures-section-research.json` binds the full source-record digest, previous crypto-resolution digest, frozen10-entry mail registry/snapshot, all top-level boundaries, chosen/omitted sections and paragraph origins, compact card, initial/current reviews, veto, normalized revision plan/identity, dispositions and10 current editor checks. No raw long-form paragraphs, quote fields, model reasoning or credentials are committed. Older paper/original/crypto-resolution records remain byte-identical.

Replay can use saved local HTML explicitly offline, or fetch the public original URL. The cloud job independently re-extracts the same full parent and61-block selection. Changed body/title/date, section structure, input-limit condition or access failures fail the live proof instead of silently refreshing reviewed text. Replay calls0 models. A synthetic registration of the genuinely completed section card must remain`not_selected` and preserve all22 mail selections,10 replacements and the full rendered body. Existing historical/crypto-resolution proofs continue to run before this new proof.

Local targeted tests cover section boundaries/subheadings/source identity, gap mapping, unknown/duplicate/reordered/partial sections, excessive input without truncation, source/date drift, subscription/time-zone holds, old context changes, missing revision history, preserved original model/editor veto, unresolved issues, new model rejection and stale editing. Static HTML/content/link checks are performed; visual browser and real email-client compatibility are not claimed. Production sending, scheduling, model secrets and history are unchanged.
