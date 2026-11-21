# Show tasks on /start

- Updated: 2026-10-24 (Asia/Taipei).
- Base: c17477d (fresh origin/main); branch codex/start-task-list; isolated .worktrees/start-task-list. Startup hook confirmed source branch current; fetched origin and used latest main because source branch predates 74 main commits. Unrelated local handoffs preserved.
- Scope: route /start through the existing task list; preserve /info help. Files: chronos/main.py, tests/test_system.py, docs/usage.md.
- Status: implementation started; validation pending. No deployment authorized.
- Next: full Python tests, documentation checks, atomic commit, PR and CI.
- Commit/push: none.

- Implemented: /start uses exactly the /task and /tasks list action, including empty state and review keyboard. /info retains help. Usage documented; webhook regression covers empty/populated parity, no AI requests and no task mutations.
- Validation: docs 87 local links in 89 Markdown files, zero missing; source compilation and diff check passed. Initial system Python lacked dependencies; switched to existing project venv. First full run failed because pytest basetemp parent did not exist; created it and reran unchanged tests. Final full run in progress.

- Final local validation: complete Python suite 1,735 passed in 109.53s; one existing Starlette deprecation warning. HTTP/AI are mocked; no live Telegram messages or task changes. Ready for atomic commit and PR; CI/merge pending, no deployment.
