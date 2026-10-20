# Step 11: selective inline replacement and comparison

`scripts/etf_mail_replace_preview.py` builds on step10's complete-email input validation and renderer. It replaces only explicitly supplied, already selected article blocks in the saved report, then presents each before/after pair followed by the entire replaced email. It remains an unsent preview; production generation, credentials, scheduling and sent history are unchanged.

## Requirements before a block can change

- Recompute a complete source/model/per-claim editorial packet. A stored PASS, stale editor, model rejection, pending source or merely readable source is insufficient.
- Pass the frozen date/history diagnostic and already exist in the original selection. Conflicting dates, old articles, future timestamps, duplicate history and unmatched candidate identities refuse the replacement.
- Match the exact material reconstructed from the original collection manifest, including its captured evidence, not just title/URL/date. A new valid-looking material and its internally consistent review cannot be substituted for a different frozen source.
- Match exactly one selected article's URL/title/source/publication identity and exactly one level-three item block containing one explicit article link. Missing or ambiguous blocks fail; no fallback silently appends or drops an article.

The adapter rebuilds the target blocks with reviewed Chinese facts, original limitations, follow-up questions, saved date and scope notes. It removes the target's generic fact placeholder and unrelated heuristic strategy mapping. No new investment recommendation or speculative strategy hypothesis is invented. Cards and original model reviews are unchanged. A fresh complete editorial record is created for each selected card, while original repair records and issue dispositions remain available.

Non-target regions are used verbatim in both reconstructions. The ledger records exact before/after blocks, offsets, hashes, packet/card/review IDs and unchanged-region hashes. The set of article URLs must remain identical. The existing delivery validator is rerun on an in-memory preview manifest with the new body hash and regenerated complete HTML; article selection, market-session records and history-added list stay unchanged. Original candidate-audit decisions must remain exactly identical. This is integrity validation of the preview, not permission to send it.

## Actual sample

| Original selected article | Change |
| --- | --- |
| GLD/GLDM fee comparison, published capture2026-10-19T22:47:32Z | Replace the generic factor-rotation mapping with the author's dated flow/fee/NAV comparisons, US-tax-context limitations and official-data/account-applicability questions. |
| TIPS/SWR Part66, published capture2026-10-19T08:00:00Z | Replace the placeholder and broad duration/credit mapping with the author's specified rate date, 30-year ladder withdrawal example, end-of-horizon depletion and spliced-return-data limitations. |

Both fit the original 2026-10-20T00:28:29.686191Z cutoff's 36-hour window and are absent from its pre-build sent history. They are already selected in the original production email, so this changes neither article ranking nor inclusion. These cards are the two articles' final step4 repaired versions, not the historical/different articles used in step7/10. The unchanged model reviews still bind their exact hashes and pass the current validator. New claim-by-claim editor records cover all15 claims. One GLD model explanation had added a specific fact-sheet day absent from that claim; that explanation is preserved in the raw review and is not copied into the card or email. New editing explicitly retains the source claim's month-only wording.

TIPS remains a truncated captured excerpt, not the full article or its charts. Its 5.05% figure is presented as the author's finite30-year ladder withdrawal arrangement, with the terminal depletion warning; it is not translated into an annual investment return or advice to the user. Gold tax statements remain attributed to the author's US IRS context and are not independently validated legal/tax guidance.

Actual result: two inline replacements, the same22 original article links, unchanged market data, zero history updates and zero model calls. The source metadata/manifest/history JSON files remain unchanged. Original content warnings remain FAILED with7 failures and119 gaps. These failures concern other selected sources and are not treated as resolved by improving the two reviewed articles.

## Validation and outputs

45 related local tests and19 subtests passed. Coverage includes preservation of other articles and market content, full before/after display, exact-source substitution rejection, selected identity mismatch, stale/rejected/pending editing, date/history refusal, block ambiguity, structural line injection, HTML escaping, read-only inputs and refusing an existing output folder. Actual-sample static checks confirmed balanced HTML,75 clickable links across comparison/full preview, all22 selected article URLs, no target placeholder/misleading mapping, and identical offline replay. The combined page is88,771 bytes with Windows newlines.

CLI output is only `replacement-preview.html` and `replacement-audit.json`, in a new directory. It does not emit sender-ready metadata. Source history and production manifests are not written. The user-facing page is a research preview with no delivery eligibility. Browser visual and actual mail-client compatibility remain unverified because the local-file preview protocol was rejected by the browser URL policy in step10; no workaround or alternative browser action is used.

```powershell
python scripts/etf_mail_replace_preview.py --bundle preview-input.json --metadata saved/metadata.json --manifest saved/collection_manifest.json --history-before saved/history_before.json --out-dir new-inline-preview
```

Remaining limitations: source faithfulness is not external factual correctness, the supplied snapshot is not an independently signed source, and the date checks describe the original capture rather than fresh current-market data. Only two reviewed blocks are improved; other original placeholders and source-quality gaps remain. An opt-in production adapter and sending authorization are separate work.
