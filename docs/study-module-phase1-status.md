# Phase 1 verification checkpoint

Updated: 2026-10-02

Phase 1 is incomplete. The current modules implement the seven-course timetable,
progress state rules, persistence methods, and a preliminary delivery service.
The owner-only webhook now accepts course replies and persists progress together
with its update receipt. Prompt/reminder scheduling is not yet wired; these
changes have not been deployed.

## Verified in this checkout

Run with the project's interpreter:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-phase1-current
```

Latest result: 135 passed, 3 dependency deprecation warnings. This includes fake-backed
Firestore tests, not a live Firestore integration test. Earlier missing-dependency
results came from a different interpreter and do not describe the project venv.

Reminders now stop at midnight in Asia/Taipei even before cleanup runs. Cleanup
expires sessions from earlier dates, including after multiple days of downtime.
Answered/missed sessions cannot return to a reminder state.

## Required before completion

Initial prompts are now wired through `study_scheduler.tick_study`, using durable
claims and receipt reconciliation. `CHRONOS_ENABLE_STUDY_TRACKING` defaults to
false. When enabled, the internal scheduler ticks once per minute; external
schedulers can call `/internal/study` with the existing scheduler secret.
Reminder dispatch and overdue-session cleanup still need runtime integration.
No live deployment or production scheduler change has occurred.

The delivery ledger now supports atomic claims in SQLite and Firestore. Competing
senders cannot claim the same key; interrupted sends become uncertain after two
minutes and are not automatically resent. Explicit rejection allows at most three
attempts with five-minute spacing. These rules are tested locally (including
SQLite concurrency and fake Firestore recreation); runtime service wiring remains.

- Persist delivery intent atomically before sending prompts/reminders. The current
  send-then-save service can duplicate a message after a crash or concurrent call;
  sequential fake tests do not prove retry-safe delivery.
- Define uncertain Telegram delivery handling: a missing response cannot establish
  that a message was not delivered. Do not promise exactly-once delivery.
- Protect progress updates against concurrent reminder writes and stale saves.
- Verify the new webhook correlation path in live delivery; local SQLite and
  fake Firestore receipt tests cover the implementation.
- Wire timezone-aware scheduling, restart recovery, bounded retries and durable
  attempt/error/next-retry state into the application.
- Verify runtime wiring and failure scenarios using the configured backends;
  report live delivery separately from mocked/local checks.
- Send the consolidated Phase 0 learning document as an actual Telegram document.
  Earlier messages containing a local repository path were not file delivery.
