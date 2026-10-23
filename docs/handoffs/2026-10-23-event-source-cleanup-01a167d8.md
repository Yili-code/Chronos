# Event request cleanup after successful booking

- Updated: 2026-10-23 (Asia/Taipei).
- Base: f8daf73; branch codex/event-source-cleanup, isolated checkout.
- Authorization: delete the original user event request once creation completes. Preserve cancellation/failure requests and success confirmation.
- Scope: durable binding of trusted incoming Telegram message ID to booking, deletion after booked state and confirmation delivery; replay and failure regressions.
- Decision: bind from the actual webhook, never AI output or inferred message order. Legacy cards without a recorded request ID cannot be safely backfilled.
- Status: implementation and validation pending; no deployment yet.

- Steering: user asked why the new OS exam is red while prior exams are purple. Cause verified in the earlier fix: exam=11 was newly introduced; historical events are unchanged. Restore exam=3 (purple) for future events without rewriting existing Calendar data.
- Implemented: trusted webhook request ID saved in durable booking state before preview delivery; successful booking confirmation triggers preview and request deletion, preserving retry receipts and booking idempotency. Legacy records safely omit request deletion. Request binding verifies chat ownership and never overwrites the original ID.
- Focused regressions: cleanup checks passed; prior distinct-color assertion conflicts with restored exam palette and was updated to the explicit mapping contract. Full validation pending.

- Validation complete: full Python suite 1495 passed in 54.07s, one existing warning; documentation links/diff passed. Coverage includes incoming request binding, booking success vs cancellation/failure, confirmation delivery failure, deletion retry without rebooking, chat isolation and identity preservation across SQLite/Firestore fake. Color change is a separate atomic commit.
