# Course habit intent audit and production release

- Updated: 2026-10-23 (Asia/Taipei).
- Base: 07fc228; branch codex/course-habit-audit, isolated worktree.
- Authorization: audit ten realistic course replies, fix incorrect recognition, complete validation and deploy to production.
- Scope: explicit recurring habits, unnumbered/English phrasing, partial updates, cancellation, reference resolution, and non-recurring progress. Preserve individual task approval and course scope.
- State: implementation underway; existing unrelated local changes excluded. Read current code and handoff; sync verified. No production change yet.
- Validation plan: record old behavior against ten fixtures, test the revised actual course-reply route on SQLite/strict Firestore, sample the real configured provider without production writes, full Python suite, PR and exact merge CI, clean-source deployment, revision/traffic/readiness checks.

- Updated: 2026-10-23T16:16:14.8918321+08:00. Implemented bounded semantic set/disable/progress/clarify decisions outside the receipt transaction. Snapshots prevent overwriting concurrent preference edits; ordinary progress bypasses AI. Missing deictic references clarify deterministically. No unapproved task or Calendar creation.
- Ten-case evidence: old numbered parser 2/10 matching intents; first revised Gemini sample 9/10 with safe schema rejection for missing reference. Added deterministic clarification and tightened review-versus-study extraction. Final gemini-3.5-flash-lite sample 10/10 including content/count checks, using nine model calls and one deterministic clarification, isolated SQLite only. Synthetic artifacts contain no secrets or real user data.
- Validation: focused suite 45 passed before adding the explicit missing-reference regression; final complete Python suite 1,463 passed in 53.29s, existing Starlette warning only. Docs 64 links / 52 Markdown files, zero missing targets; diff check passed. An early transport fixture incorrectly passed a dict instead of an HTTP handler; corrected to exercise real request/response transport.
- Production readback before release: chronos-audit-07fc228 serves 100% with release 07fc2289e4eae44f339278527ea10988ab12ee8e; service template matches that revision. Deploy only after PR and exact merged-SHA CI pass. No production changes yet.
