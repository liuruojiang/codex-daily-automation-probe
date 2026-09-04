# Frozen stale-OHLCV regression fixture

`sina_000905_index.csv` is the existing real Sina index-history snapshot used by
`test_ohlcv_provider_validation.py::test_real_frozen_history_is_still_rejected_when_stale`
in the pinned ICIM strategy release. It has 5,250 rows, 2005-01-04 through
2026-08-14. It is deliberately stale relative to that test's 2026-09-04 clock.
It is test-only data, not a runtime fallback and must never be used as today's feed.

Copied unchanged from the existing ICIM data snapshot, not synthesized or refreshed.
Original byte SHA-256: `bbee1ef41f0fe0445398a0f51ff98375c766af22f0b0f92f5409dd6ef8456c35`.
LF-normalized SHA-256 (portable Git checkout assertion):
`c5121b044133099e250fd5e5e803c447bf8811e5a4ae8cf8f19e2b8f5c2ddcfd`.

CI copies this fixture only into its disposable strategy-test checkout. Production
signal jobs do not copy it. Missing or changed fixture data is a failed regression,
not a skipped test.
