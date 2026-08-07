# Microcap Corrected Digest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair v2.5 realtime publication, show member rebalances in the compact digest, add idempotent redundant scheduling, and send one verified corrected 2026-08-07 email.

**Architecture:** The microcap repository owns v2.5-native signal schema compatibility. The automation repository owns action rendering, workflow scheduling, delivery markers, and correction dispatch. GitHub Actions artifacts provide the per-date delivery marker and workflow concurrency serializes redundant runs.

**Tech Stack:** Python 3.11, pandas, pytest, GitHub Actions YAML, GitHub REST API, Gmail SMTP.

---

### Task 1: v2.5 native holding compatibility

**Files:**
- Modify: `microcap_top100_mom16_biweekly_live_v2_5.py`
- Create: `tests/test_v2_5_realtime_signal_label_compat.py`

- [ ] Add a failing test that calls `_build_signal_row` with `holding=next_holding=long_microcap_top100` and asserts the published row retains that native label.
- [ ] Run `python -B -m pytest -q -p no:cacheprovider tests/test_v2_5_realtime_signal_label_compat.py` and verify the existing normalizer raises `ValueError: unexpected holding labels`.
- [ ] Add `_v2_0_signal_compat_net_df` to translate only the copied dataframe passed to the legacy builder, then restore native holding, turnover, hedge-ratio, cost, and leverage fields from the real v2.5 row.
- [ ] Re-run the test and the v2.5 compatibility/import checks; commit only the implementation and regression test.

### Task 2: Member rebalance rendering

**Files:**
- Modify: `scripts/build_microcap_realtime_digest.py`
- Modify: `tests/test_microcap_digest_email_body.py`

- [ ] Add a failing regression using the observed fields `member_rebalance_required=True`, `member_enter_count=7`, `member_exit_count=7`, and `member_rebalance_label=名单调仓（调入 7，调出 7）` while position and scale both hold.
- [ ] Verify the current result is `无操作` and the subject/conclusion omit the required trade.
- [ ] Update action rendering so active-position member changes are shown, combined with scale changes when both occur, and counted by the subject/conclusion logic.
- [ ] Run the targeted digest tests and commit the focused change.

### Task 3: Idempotent redundant schedule and corrected dispatch

**Files:**
- Create: `scripts/check_microcap_delivery.py`
- Create: `tests/test_microcap_delivery_gate.py`
- Modify: `.github/workflows/microcap-realtime-digest.yml`
- Modify: `scripts/build_microcap_realtime_digest.py`
- Modify: `tests/test_microcap_workflow_refresh_gate.py`

- [ ] Add failing unit tests for Beijing delivery-date marker naming, existing-marker detection, and `correction=true` bypass.
- [ ] Implement the GitHub Actions artifact lookup using `urllib.request` and emit `should_send`, `delivery_date`, and `marker_name` to `GITHUB_OUTPUT`.
- [ ] Add three off-minute cron entries, one non-cancelling concurrency group, `actions: read`, an early delivery-gate step, conditional heavy steps, and a post-send marker upload.
- [ ] Add workflow-dispatch `correction` input and pass `--subject-prefix 纠正版` to the digest builder only for a corrected manual run.
- [ ] Run unit tests, YAML/static workflow tests, and `git diff --check`; commit the schedule/delivery change.

### Task 4: Remote integration and production proof

**Files:**
- Delete before merge: the completed spec and plan documents from the automation feature branch.

- [ ] Run the relevant test suites fresh in both worktrees and confirm clean git diffs.
- [ ] Push both branches, create focused PRs, merge the microcap PR first, then the automation PR.
- [ ] Fetch both remotes and verify local `origin/main` SHAs match GitHub.
- [ ] Dispatch `microcap-realtime-digest.yml` with `correction=true` and wait for completion.
- [ ] Download the production artifact; verify v2.0, v2.3, and v2.5 exit code 0, all three CSVs exist, the latest anchor is 2026-08-06, quote date is 2026-08-07, coverage is 100/100, and member trades appear in the Markdown.
- [ ] Read the received Gmail message and confirm the corrected subject/body match the artifact.
- [ ] Remove temporary artifacts, completed worktrees, and merged local branches while preserving unrelated user changes and worktrees.
