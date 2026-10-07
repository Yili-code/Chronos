# Phase 1 operation and verification

Course tracking is controlled by `CHRONOS_ENABLE_STUDY_TRACKING` (default false).
Production was explicitly activated on 2026-10-05; see the current status record.
Do not confuse passing local tests with deployment or production verification.

## Configuration

- Production state: `CHRONOS_DATABASE_BACKEND=firestore`; use the existing project,
  database and collection prefix. Never copy school credentials into this config.
- Delivery: the existing owner-only Telegram bot and configured chat ID.
- Local continuous process: enable internal scheduler and study tracking. One
  minute tick handles due prompts, reminders, overdue cleanup and failure notices.
- Cloud Run: disable internal scheduler; configure an external minute schedule
  that POSTs `/internal/study` with the existing scheduler secret header. Protect
  the secret in the deployment system; do not paste it in logs or documents.
- Set `CHRONOS_ENABLE_STUDY_TRACKING=false` to stop scheduled study delivery.
  Existing persisted course records are preserved.
- The main deployment script accepts `-EnableStudyTracking` and
  `-DisableStudyTracking`. With neither switch, it preserves an existing
  explicit `true` value so an unrelated deployment cannot silently stop course
  prompts. A new service remains disabled until explicitly enabled.

The general deployment script provisions the daily task job only. The separately
authorized `scripts/activate_course_schedule.py` provisions or updates the
`chronos-course-progress` job using the existing service's secret binding, and
executes two current-time ticks. It may send due course notifications; run only
when activation is authorized, not as a read-only diagnostic. It prints only
allowlisted evidence and never secret-bearing response bodies. Production
activation has been verified; no AI companion worker is required.

## Verification sequence

1. Run the full suite with the repository venv and a workspace-local pytest temp
   directory. Inspect the tests, not just the count.
2. Confirm Firestore authentication independently of gcloud CLI login: Python
   uses Application Default Credentials or the runtime service identity.
3. Use an explicitly isolated test collection prefix for integration checks.
   Verify session creation, duplicate-create preservation, transactional progress
   plus webhook receipt, and readback through a newly constructed repository.
4. Verify scheduler authentication: missing/wrong header is rejected and disabled
   study tracking does not send any message.
5. Send one clearly labeled owner-only integration prompt. Verify the returned
   Telegram message ID and persist it before testing a reply. A mock reply is not
   proof of a real webhook arriving from Telegram.
6. Verify actual reply correlation and persistence, then verify reminders stop.
   Keep accelerated-clock tests local; do not spam the production chat to simulate
   multiple class days.
7. Verify failure notices and inspect safe delivery states. A transport timeout
   means outcome unknown, not proof of non-delivery. No automatic resend occurs
   after an uncertain send; investigation is required.

## Operational limits

- New scheduled sessions atomically create a survey task. A valid same-day reply
  atomically completes it and creates one review task alongside the webhook
  receipt. Review completion is manual via `/done`; neither task receives an
  invented deadline. This flow uses no AI and does not resume Phase 2.
- Historical sessions are not backfilled. Cleared tasks are not resurrected.
  Missed surveys stay open until manually closed; a late reply does not invent
  a review task. The scheduler currently uses the weekly timetable, not holidays.

- Initial prompts catch up only for the current Taipei date. Reminders are spaced
  from their recorded preceding delivery, never sent across dates.
- A reminder already in flight can arrive after a reply; its completion cannot
  overwrite the answered state.
- Notice delivery uses the same Telegram transport. If that transport is down,
  `failure_notices_unresolved` is returned by the tick for external monitoring;
  there is no independent alert channel or recursive notice loop.
- No automatic CAS login, TronClass mutation, PDF processing or Gemini call is
  part of Phase 1 scheduling.

`course_tracking_service` is a historical fake-driven prototype. The live
application path is `main.run_study_tick` → `study_scheduler.tick_study` → durable
SQLite/Firestore delivery claims and session transitions.
