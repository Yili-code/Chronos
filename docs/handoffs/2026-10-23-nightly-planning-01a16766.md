# Nightly oldest-task proposals

- Updated: 2026-10-23T14:21:00+08:00.
- Base: d4f6136; branch: codex/nightly-oldest-tasks; isolated worktree.
- Scope: daily 21:00 Asia/Taipei selection of four oldest eligible open tasks, durable daily receipt, existing approval queue, local/cloud scheduling and tests.
- Decision: oldest means created_at, with ID tie-break; exclude existing planning/booking/feedback/backward-managed/rejected work. Fewer than four means all eligible. Accept/Allow remains required before Calendar writes. Preserve existing active turn and show proposals sequentially.
- Implemented: durable daily selection, authenticated nightly endpoint, one-at-a-time approval continuation across quiet hours/midnight, local cron and verified Cloud Scheduler provisioning (resume paused jobs and validate schedule/timezone/URI).
- Validation: full Python suite 1,235 passed in 52.92s; then added rollover and uncertain-delivery cases, final focused suite 29 passed in 1.86s. Strict Firestore fake and SQLite covered. Existing Starlette deprecation warning only. Documentation: 60 links in 41 Markdown files, zero missing; diff check and PowerShell parser passed.
- Environment: sandbox temporary-file permissions stalled initial focused run; stopped only this task's matching processes and reran with normal Windows permissions. No test failure was bypassed.
- Limits: external calls mocked; no live Calendar writes, Telegram delivery, cloud job activation or deployment. Deployment requires separate authorization.
- Publication in progress: next commit/push PR, verify all required CI jobs before merge. Other chats remain in the original checkout and are excluded.
