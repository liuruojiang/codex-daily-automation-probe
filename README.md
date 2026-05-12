# Codex Daily Automation Probe

This private repository tests whether a real cloud scheduler can run independently of the local Codex Desktop app.

The workflow `.github/workflows/scheduler-probe.yml` runs at 07:05 Asia/Shanghai and writes the actual trigger time to a GitHub issue comment.
