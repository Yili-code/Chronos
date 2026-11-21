# Today remaining agenda

- Updated: 2026-10-24 (Asia/Taipei).
- Base: c17477d; branch codex/today-upcoming; isolated checkout .worktrees/today-upcoming.
- Scope: /today queries now through next local midnight, preserving ongoing/all-day overlapping events. Tomorrow and evening delivery remain full-day.
- Files: chronos/evening_agenda.py, tests/test_evening_agenda.py, docs/usage.md and this handoff.
- Sync: fetched origin; parent branch 74 commits behind origin/main with unrelated local handoffs; preserved all parent work and based isolated branch on origin/main.
- Implementation complete; 25 focused agenda tests passed (both database fixtures); docs check 87 links/89 Markdown files passed; diff check passed. No AI routing, database, or Telegram dispatch changes.
- No cancelled alternatives; no deployment authorized. Next: atomic commit/PR and CI. Initial restricted test runs encountered temporary-directory permissions; normal-permission run passed.
- Commit/push: pending. No live Calendar/Telegram mutations.
