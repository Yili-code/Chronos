# Review a duration edit against the live Calendar booking

- Started 2026-11-16 Asia/Taipei from `1962cee`, on `codex/booking-drift-review` in an isolated worktree. The prior read-only diagnosis verified matching booking identity and differing saved/live time windows.
- Scope: preview a duration-only adjustment to a verified booking whose time has drifted, with explicit approval bound to the task, live interval and Calendar version. Only a strict subset of the live interval may be applied, so pre-existing overlaps cannot block a shrink or grow. Normal moves retain their existing availability rules. The existing guarded write reconciles Calendar and Chronos, including the original and new daily budget.
- Verified 354 targeted and 4,675 full Python tests, plus documentation and diff checks. Cases include a different live date, version changes before confirmation and during the write, and expired approval across SQLite and Firestore. No live booking edits. Publication and CI pending.
