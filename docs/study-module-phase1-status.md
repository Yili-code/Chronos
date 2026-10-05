# Phase 1 status

## Tracking-only production activation — 2026-10-05

Owner explicitly authorized deployment and scheduling. Source commit `e636b64`
is deployed as Cloud Run revision `chronos-00020-bqn`, serving 100% of traffic.
Study tracking is enabled; the internal scheduler remains disabled. The external
`chronos-course-progress` job runs every minute in `Asia/Taipei`, using the
existing scheduler-secret binding. The separate daily-tasks job is unchanged.
This supersedes the earlier activation-off statements below, not the AI deferral.

Verified live: health OK; unauthenticated Study POST rejected with 403; first
authenticated tick reconciled one session and the immediately repeated tick
reconciled zero. The current course session and linked survey task exist in
production Firestore, with the session pending. Cloud Scheduler completion logs
at 06:22:02Z and 06:22:09Z report HTTP 200. A real owner reply completing the survey
and creating the review task remains the final user acceptance check.

An initial revision failed to boot because PowerShell combined two environment
arguments into one invalid boolean. Quoting the combined argument corrected it;
the successful revision above is the active one. No secrets were changed.

AI generation remains deferred, no AI worker was started, and no lecture content
was uploaded. Holiday and term-date exclusions are still not implemented.

## Survey and review tasks — 2026-10-05

Owner selected both task types: new scheduled prompts create one survey task;
valid replies complete that task and create one manually completed review task.
The session and task writes are transactional in SQLite and Firestore, and
reply writes share the Telegram update receipt transaction. Duplicate deliveries
and replies do not duplicate tasks. This path does not invoke AI generation.

Full offline regression after implementation: 341 passed, 525 dependency
deprecation warnings (30.74 seconds). A subsequent SQLite rollback test was added
to exercise an injected persistence failure; focused verification is recorded
in the commit. Firestore tests use a fake that rejects reads after writes;
these are not live Firestore transaction or Telegram delivery evidence.

Not deployed or activated by this change. Existing AI-generation deferral stays
in force. The next release step is a tracking-only deployment plus protected
Cloud Scheduler activation and a real owner reply check. Scheduling currently
follows the weekly timetable, without holiday/term-date exclusions; do not claim
calendar-aware behavior. No unrelated production daily-task job was changed.

The older verification records below predate the task integration.

Phase 1 implementation is complete; production activation remains off.

See [acceptance audit](study-module-phase1-acceptance.md) for the requirement-by-requirement check,
[live evidence](study-module-phase1-live-evidence.md) for Firestore and Telegram results,
and [operations](study-module-phase1-operations.md) for activation and Phase 5 checks.

Final local verification: 159 tests passed, three dependency deprecation warnings.
The Phase 0 interview-preparation Markdown document was delivered as an actual
Telegram attachment. Production deployment and real inbound production webhook
verification are not claimed.
