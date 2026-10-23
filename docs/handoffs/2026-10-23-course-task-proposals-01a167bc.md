# Course-specific task proposals

- Updated: 2026-10-23 (Asia/Taipei).
- Base: 7dc2264; branch codex/course-task-proposals, isolated worktree.
- Scope: persistent course habits, two after-class task suggestions requiring individual acceptance. User confirmed competition course only; other courses retain progress prompts.
- Sync: verified tracking current after retry with normal Git access; unrelated local edits excluded. General preference work in natural-audit is uncommitted and excluded.
- Plan: explicit numbered recurring replies and management command; course-specific defaults; durable delivery and atomic per-item approval on SQLite/Firestore. No inferred deadlines, auto-created study tasks, or Calendar bookings.
- Status: implementation underway. Full suite, PR CI and publication pending. No deployment authorized.
- Progress: implemented course-specific durable proposals, individual approval/skip, seven-day expiration, explicit numbered preference learning and management command. Focused SQLite/strict Firestore tests plus actual webhook replay pass; integrating newly published main before final validation.

- Updated: 2026-10-23T15:59:39.0186533+08:00. Integrated origin/main 4cdc18d, resolving only additive imports in main.py. Full combined Python suite: 1,419 passed in 56.64s, existing Starlette warning only. Initial feature baseline: 1,360 passed; final added webhook test independently passed before integrated full run. Docs: 61 links / 50 Markdown files, zero missing targets; diff check passed.
- Decisions: only competition course has an enabled default; other courses can explicitly opt in through a numbered recurring reply or /coursehabit. Immutable issued proposals survive preference changes; each item expires after seven days and requires matching owner/message. No inferred deadline, Calendar booking or unapproved task creation. Initial test failures were a missing temporary parent and reused fake Telegram IDs for morning/evening sessions; corrected fixtures preserve real multi-course catch-up behavior.
- Limit: no live Telegram delivery or production activation measured. Pending publication and current-head CI; deployment outside authorization.
