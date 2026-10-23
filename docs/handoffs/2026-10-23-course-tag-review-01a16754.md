# Task wording and confirmed course tags

- Updated: 2026-10-23 14:20 +08:00 (Asia/Taipei).
- Base: a87203f47af785d6221a142cd846f87dd16a66b7; branch codex/course-tag-review in isolated managed worktree. Startup hook reports original checkout history current; task-batch edits are uncommitted and excluded.
- Scope: natural task wording and course-based tag proposals with explicit owner approval. Existing tags preserved; task content and scheduling untouched.
- Plan: add a bounded AI metadata suggestion contract using the seven actual courses, durable per-task confirmation cards, task-list review entry, and creation-time course-tag suggestions. Full Python validation and PR CI required; no deployment or live task mutation authorized.
- Implemented: task_review schema and provider contract; shared natural title wording; explicit course inference with pending cards on creation; existing-task review via list button, /reviewtasks and natural intent; preserve existing tags/aliases and control-task titles; bound/expiring/snapshot-checked approval, cancellation and undo; delivery retry receipt support.
- Verification: isolated baseline full Python suite 1,256 passed (47.63s); docs 60 local links in 40 Markdown files, zero missing; source compilation/diff check passed. Earlier full run exposed one obsolete no-keyboard assertion (1 failed/1,248 passed); replaced it with exact review-keyboard contract. Provider test uses real structured-output transport mock; live semantic accuracy/delivery not measured.
- Integration: remote main advanced to ee606d9 with batch task creation and no-change replies during implementation. Preserve this validated checkpoint, integrate those commits, adapt creation-time suggestions to batches, then repeat full validation before publishing. No deployment or live task changes.
- Status: local commit/publication pending. Rejected approach: inferring and silently saving course tags. Original checkout remains untouched.
