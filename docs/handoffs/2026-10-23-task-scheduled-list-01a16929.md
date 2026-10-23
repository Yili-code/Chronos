# Scheduled task list groups

- Updated: 2026-10-23 (Asia/Taipei).
- Base: origin/main 0e839a5; branch codex/task-scheduled-list; isolated worktree.
- Goal: selected option 10, Scheduled and Unscheduled sections with booked time ranges and deadlines.
- Decision: confirmed scheduled/awaiting_feedback states expose stored start/end; proposed/creating states remain unscheduled. Existing timing.scheduled retains its planned-time display. Preserve command positions and deadline ordering within groups. Replace former course grouping as requested by the two-section design.
- Files: chronos/tasks.py, task rendering tests, usage documentation.
- Sync: elevated manual check succeeded; original unrelated local work preserved.
- Validation: full Python suite 1597 passed (105.37s), one existing Starlette deprecation warning; focused task/timing suite 82 passed, updated exact-format assertions 32 passed; docs 73 Markdown files/86 links with zero missing targets; git diff --check passed. Initial suite setup required creating the temp parent; a detected planning_status compatibility failure was fixed without weakening its assertion.
- Commit/push: preparing atomic commit and PR. CI and merge pending. Deployment not authorized.

- Evidence updated: 2026-10-23T22:43:05.000846+08:00.
