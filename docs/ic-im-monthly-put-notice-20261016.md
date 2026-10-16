# Monthly Put maintenance reminders, 2026-10-16

The existing monthly bought-Put maintenance still takes place at the maintenance-day close. The prior session's close digest and the maintenance-day intraday digest now show a prominent reminder before holdings. The subject and headline explicitly say monthly Put maintenance is due even when all regular actions are HOLD.

Users determine the new contract and quantity near that day's close using then-current market data and the existing strategy configuration, and complete any required rollover at that close. No contract is locked by this reminder; zero configured Put exposure does not create a position. A reminder is not a fill or an account instruction. Historical replay remains labelled historical.

The helper consumes the producer's explicit maintenance and next-session dates. It does not change selection, quantities, monthly execution timing, settlement/recovery, identity, ledger state, delivery markers, schedules, SMTP, or the pinned strategy commit. It derives its own notices from validated per-product signals rather than trusting free-form top-level notice text.

Validation: the existing daily-digest suite and seven new reminder cases pass locally (33 tests plus 10 subtests). These cover prior close, same-day intraday, HOLD visibility, HTML/plain/subject placement, stale calendars, historical replay, no after-close prospective reminder, non-mutation, date-object handling, and calendar-only reminders in partial-signal warnings without publishing unconfirmed targets. The strategy-side isolated suite includes 24 new reminder cases. Release CI and final deployed SHA are recorded separately; no test email or production rerun is required by this change.
