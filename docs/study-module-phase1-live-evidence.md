# Phase 1 live verification evidence

## Firestore — 2026-10-02

Executed `scripts/verify_study_firestore.py` with the repository venv, existing
gcloud identity, configured project and a fresh `study_verify_<UUID>` collection
prefix. No production task/course collection was changed.

Observed result:

```json
{"live_firestore": true, "cleanup_complete": true}
```

Verified using the actual Firestore SDK/service:

- deterministic course-session create and duplicate-create preservation;
- course progress and Telegram update receipt in the same transaction;
- repeated update ID returns its original receipt without reapplying the action;
- a new repository instance reads the persisted reported progress;
- durable delivery claim rejects a second claimant and records a sent result.

Cleanup deleted only the three synthetic documents created by this check. The
test documents were intentionally disposable; cleanup is not a recovery mechanism
for production data. Credentials were held only in process memory, with stdout
restricted to boolean evidence and exception class names.

This does not verify an actual Telegram webhook, Cloud Scheduler invocation,
production deployment, or concurrent Firestore contention. Those must not be
inferred from this successful persistence check.

## Telegram — 2026-10-02

Ran `scripts/verify_study_telegram.py` using the configured Chronos owner chat.
Two clearly labeled integration-test messages were sent: an initial message and
a second message using the first message's ID in Telegram reply_parameters.

```json
{"initial_delivery": true, "reply_reference_delivery": true}
```

No webhook configuration or polling state was changed. This verifies actual
outbound delivery and reply references. Incoming webhook correlation is covered
by the application's local HTTP tests and actual Firestore receipt tests; a real
human reply through the public production webhook has not been tested for this
revision. Production deployment and Cloud Scheduler activation remain Phase 5
verification, and must not be represented as completed by these checks.
