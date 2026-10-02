# Phase 1 implementation acceptance

Phase 1 course tracking is implemented. Continuous production activation is off.
The distinction follows the PRD: Phase 1 delivers course tracking; Phase 5 covers
deployment, real webhook and external integration verification in production.

## Requirement audit

| PRD Phase 1 requirement | Implementation | Evidence |
|---|---|---|
| Seven-course timetable | course_tracking.COURSE_SCHEDULE | Every slot tested at one minute before and at end + five minutes |
| Post-class prompt | study_scheduler.tick_study | Local scheduler tests and live Telegram delivery |
| Reply correlation | main.telegram_webhook, record_course_reply | Original prompt ID, owner-chat guard, duplicate update receipts, unknown-reply rejection |
| Reminder mechanism | Durable reminder delivery keys and atomic session transitions | Maximum two, one-hour spacing, stop after answer, delayed restart, midnight expiry |
| Firestore state | FirestoreDatabase sessions, receipts, delivery ledger | Fake lifecycle tests plus isolated live create/reply/readback/claim verification |
| Failure behavior | Safe outcome parser, bounded retries, durable notices | Malformed responses, uncertain sends, concurrent claims, notice deduplication |
| Secrets and read-only scope | No school credentials or TronClass operations; transport logs disabled | Narrow state payloads; exception text and raw API responses excluded from live probes |

## Evidence boundaries

The repository test suite passes in the project virtual environment. Firestore
live checks used only synthetic documents under an isolated collection prefix;
those documents were removed. Chronos owner-only Telegram sends succeeded.

No deployment, enabled recurring production study job, real inbound production
Reply, holiday suppression, PDF selection or Gemini summary is claimed here.
Holiday behavior belongs to Phase 4 and summary behavior to Phase 2. The default
feature flag stays off until the operator performs the activation steps.

Operational constraints: Telegram does not offer an idempotency key for sends.
An uncertain send is not retried automatically. A request already in flight may
arrive after a user's answer, but cannot overwrite that answer. Failure notices
use the same transport, with unresolved counts available to scheduler monitoring.

See study-module-phase1-live-evidence.md and study-module-phase1-operations.md
for exact live-check scope and activation procedure.
