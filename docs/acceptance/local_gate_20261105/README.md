# Local gate completion, 2026-11-05

These are archival copies of the actually installed local launcher/scheduling data and their exact evidence, not cloud production entrypoints or a new CI suite.

The original flow9 core30 hit its 300-second deadline. Four independent groups succeeded. Recorded per-node runtimes showed stale scheduling weights and uneven work; only PROFILE_SOURCE/PROFILE_NODE_SECONDS changed. The worker executable AST excluding these two literal assignments is identical. All 1081 collected nodes, assertions, offline guards and the whole-process-tree deadline remain unchanged.

The existing exact-evidence core-resume path now binds this original failed run and the reviewed old/new scheduling-worker hashes. All other source, fixture, consumer, environment, config, original archived evidence and completed-process checks remain enforced. A new full core30 ran once; no partial core shard counted as success. Four successful groups were independently reverified and reused. 42 focused launcher counterexamples passed. Real core30:1081 passed, zero failures/errors/skips,218.549s. The complete maintenance gate passed and activate returned0 at23:21:37+08:00.

Prepared D:/CodexDaily/flow9; original D:/ct/icim_gate_20261105/flow9_acceptance; resumed D:/ct/icim_gate_20261105/flow9_core_resume. Lifecycle dc101208aa6f4f4bbb2d4675e92bb7e2 and4cca8709505c468b914185a028724a90 retain original and resumed evidence and cleaned owned temporary fixtures. Never rerun this archived test file as an ordinary cloud test; it intentionally references the actual local acceptance evidence.

Daily still runs its complete required tests. This is software readiness only: live market data, state, publication and email delivery remain independently gated. No strategy/history/SMTP changes.