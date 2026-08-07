# Compact Microcap Email Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy a concise, decision-first Microcap Top100 email while preserving complete raw outputs in the GitHub Actions artifact.

**Architecture:** Extend the existing digest builder with a small parser that merges stdout key/value fields with the optional one-row realtime CSV. Render one localized row per strategy version, derive the subject action tag from status and position/scale changes, and emit only active warnings plus the run link. The workflow passes the already-generated realtime CSV paths explicitly.

**Tech Stack:** Python 3 standard library (`argparse`, `csv`, `json`, `re`, `pathlib`), GitHub Actions YAML, `unittest`/`pytest`.

---

### Task 1: Define the compact email contract with failing tests

**Files:**
- Modify: `tests/test_microcap_digest_email_body.py`

- [ ] **Step 1: Replace the full-body expectation with an actionable three-version fixture**

Create stdout fixtures for v2.0/v2.3/v2.5 and one-row CSV fixtures containing `next_session_actionable_scale` and active overheat fields. Invoke `digest.main()` with version-qualified `--signal-csv` arguments.

- [ ] **Step 2: Assert the approved content and suppressed diagnostics**

Assert `[需操作]`, the one-sentence conclusion, localized holdings, execution scales, the v2.0 microcap/hedge/gap values, the v2.3 hedged-spread WLS label, the v2.5 microcap WLS label, and active v2.0/v2.3 warnings. Assert that `原始实时信号输出`, `脚本退出码`, internal result paths, and raw `strategy_version:` lines are absent.

- [ ] **Step 3: Add no-action and abnormal-subject tests**

Use a no-action fixture to require `[无需操作]`. Keep the stale-anchor fixture but update it to require `[异常]` and a visible concise reason.

- [ ] **Step 4: Run the focused tests and verify RED**

Run: `python -m pytest tests/test_microcap_digest_email_body.py -q`

Expected: failures because `--signal-csv`, Chinese action subjects, compact rendering, and risk summaries do not yet exist.

### Task 2: Implement compact parsing and rendering

**Files:**
- Modify: `scripts/build_microcap_realtime_digest.py`

- [ ] **Step 1: Add optional CSV parsing**

Import `csv`, parse repeated version-qualified `--signal-csv` values, read the final CSV row when present, and merge it with stdout key/value fields without making CSV availability a hard failure.

- [ ] **Step 2: Add localized presentation helpers**

Implement helpers for holding labels, boolean/number parsing, next-session scale selection, version-specific momentum text, per-version action detection, overall subject tag, and concise risk warnings.

- [ ] **Step 3: Replace the verbose body renderer**

Render the conclusion, five-column table, attention section, shared data time, and GitHub Run link. Keep the generated Markdown artifact compact and set `attachment` to `None`; do not embed raw stdout.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `python -m pytest tests/test_microcap_digest_email_body.py -q`

Expected: all compact email tests pass.

### Task 3: Wire the workflow to structured signal files

**Files:**
- Modify: `.github/workflows/microcap-realtime-digest.yml`
- Test: `tests/test_microcap_workflow_refresh_gate.py`

- [ ] **Step 1: Add a failing workflow assertion**

Require the build command to pass all three version-qualified `--signal-csv` paths.

- [ ] **Step 2: Run the workflow test and verify RED**

Run: `python -m pytest tests/test_microcap_workflow_refresh_gate.py -q`

Expected: failure because the three arguments are absent.

- [ ] **Step 3: Add the three workflow arguments**

Pass the existing output paths:

```text
v2.0=microcap/outputs/microcap_top100_mom16_biweekly_live_v2_0_realtime_signal.csv
v2.3=microcap/outputs/microcap_top100_mom16_biweekly_live_v2_3_realtime_signal.csv
v2.5=microcap/outputs/microcap_top100_mom16_biweekly_live_v2_5_realtime_signal.csv
```

- [ ] **Step 4: Run both microcap test modules**

Run: `python -m pytest tests/test_microcap_digest_email_body.py tests/test_microcap_workflow_refresh_gate.py -q`

Expected: all microcap tests pass.

### Task 4: Verify and deploy

**Files:**
- Verify all modified files and generated fixture output.

- [ ] **Step 1: Generate and inspect a representative metadata file**

Run the builder with the test fixture inputs and verify the subject tag, concise body, risk wording, and absence of raw stdout.

- [ ] **Step 2: Run syntax and whitespace checks**

Run:

```text
python -m py_compile scripts/build_microcap_realtime_digest.py tests/test_microcap_digest_email_body.py tests/test_microcap_workflow_refresh_gate.py
git diff --check
```

Expected: exit code 0 for both commands.

- [ ] **Step 3: Run the scoped regression suite**

Run: `python -m pytest tests/test_microcap_digest_email_body.py tests/test_microcap_workflow_refresh_gate.py -q`

Expected: all microcap tests pass. Record the unrelated date-sensitive ETF baseline failure separately and do not modify that module.

- [ ] **Step 4: Commit, push, and activate**

Stage only the spec, plan, builder, microcap workflow, and microcap tests. Commit on `codex/compact-microcap-digest`, push the branch, integrate it into the production `main`, and verify the remote `main` contains the compact renderer before the next scheduled run.
