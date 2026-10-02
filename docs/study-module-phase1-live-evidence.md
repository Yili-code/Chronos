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
