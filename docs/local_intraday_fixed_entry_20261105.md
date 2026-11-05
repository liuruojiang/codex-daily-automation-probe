# Local intraday entry and structured rendering

The normal daily gate remains mandatory, including all five suites and its
source/fixture/consumer bindings. The new entry only coordinates the production
steps after that gate. It never sends mail, applies a ledger candidate, restores
state, or rebuilds history. Scheduling is unchanged.

Install an immutable bundle of this commit's tracked `scripts/` files into a
new directory. Bind every file's raw SHA256 in `manifest.json` (schema 1, files
mapping); atomically point HOME/local_intraday_install.json to its `bundle` and
`manifest_sha256`. Preserve the previous pointer as a rollback record. The entry
checks its own and every bundled consumer's bytes before native work starts.
The existing certified daily helper and active snapshot remain separate.

After the real daily gate passes:

```text
python -B -X utf8 <bundle>/scripts/run_local_intraday.py
  --automation-home <automation HOME> --gate <today daily gate.json>
  --resident <today verified morning resident directory>
  --ledger-source <official source specification.json> --out <new run directory>
```

The source specification contains exactly four original source paths:
`candidate`, `archive`, `artifact_metadata`, `run_metadata`. Derive these from
the morning preparation's actual adoption command/receipt; never reuse an old
date by editing it. The official adopter performs a new dry-run and full-chain
check. The local ledger must already cover D; legitimate remote forks preserve
the valid local chain. Necessary ledger preparation remains a separate native
operation under the original rules.

IC/IM and Microcap run independently; Microcap's original readiness stages and
three CLIs remain serial. A ready event points to the validated summary, full
report and input/output hashes. The Codex task must publish that strategy's
summary immediately, without waiting for the other strategy. Microcap's actual
publication must be before 15:00. Ready files explicitly say not published.
Unknown or failed commands keep their receipts and actor state; the durable
daily claim prevents an automatic second orchestration. Inspect the failed stage
and use the original bounded component recovery, never delete a claim to rerun
successful work. Shutdown failure must not conceal a previously ready report.

`build_local_intraday_report.py --mode replay` can re-render saved results for
review, explicitly ineligible for new intraday publication. Live mode uses the
same formal consumers and original readiness evidence. No dates, positions or
market values are manually transcribed. ETF Put strikes use yuan, index Put
strikes use points. Missing data and partial targets stay explicit.

Verification uses today's saved real result/three CSV/readiness and isolated
counterexamples. No new quote collection, ledger writes, SMTP, intent or marker
changes are part of these tests. Future live delivery remains separately verified.
