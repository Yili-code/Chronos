# Study-plan revisions

- Updated: 2026-10-24 (Asia/Taipei).
- Base: 241f6fa; branch codex/study-plan-revision; isolated worktree .worktrees/study-plan-revision.
- Authorization: fix screenshot behavior and deploy after full validation and PR CI.
- Scope: distinct study revision intent, recent tomorrow agenda context, per-task minimums and unfinished tasks, whole-day feasible proposal retaining other study bookings. Reuse versioned Calendar review/reconciliation; never infer completion.
- Validation pending: both backends, routing, duration/capacity/deadline, stale approval and replay; complete Python and extension suites, exact merge CI and guarded deployment.
- Commit/push/deployment: none. Other local work preserved.
- Development evidence: 35 new regression cases passed across SQLite and Firestore, including real routing fallback, exact schema transport, minimum preservation, chapter-omission protection, capacity/deadline rejection, stale review, cancel/expiry, all-new sessions, and uncertain-move recovery without duplicate successful patches.
- Initial complete suite: 1,819 passed in 122.45s before final chapter-omission and synchronized-duration refinements. Final-tree complete suite running. Extension 21 passed; docs 87 links / 98 Markdown files, zero missing; diff check passed.
- Live parser check with synthetic tasks only: first call did not return the expected complete chapter set; a second call returned all five chapters at 120 minutes. Added literal course/chapter scope validation and conservative expansion from the explicit per-chapter minimum so a model omission cannot drop a matched chapter. No real task, Telegram or Calendar writes from these checks.
- Environment: first focused test attempt lacked the basetemp parent; created dedicated parent and reran. Tests need approved Windows asyncio local socket access. Local ruff module unavailable; required CI owns undefined-name check.
- Review scope: new unfinished sessions enter the existing booking queue after Allow; scheduled sessions use existing ETag/reconciliation. Whole-plan overflow asks which task may move to another day and leaves original events unchanged. No mathematical optimality claim for slot search. Existing longer sessions are never shortened.
- Final-tree local validation: complete Python suite 1,821 passed in 121.15 seconds; one existing Starlette deprecation warning. Source compilation, diff and documentation checks passed; extension 21 passed. Final synthetic live classifier selected study_replan with no clarification; live parser correctly resolved tomorrow and five ISPM chapters at 120 minutes on the successful check. These are synthetic provider checks, not a live booking interaction.
- Remote main re-fetched before publication and remains 241f6fa. Preparing atomic commit/PR; required remote CI, merge-source CI, deployment and runtime readback pending.
