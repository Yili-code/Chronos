# Compact today agenda

- Updated: 2026-10-24 (Asia/Taipei). Base 4a18f2c; branch codex/today-format; isolated worktree .worktrees/today-format. Remote fetched; unrelated parent changes preserved.
- Request: date-only heading, one event per line, time range and title; omit duration and task ID for /today.
- Scope: chronos/evening_agenda.py, tests/test_evening_agenda.py, this handoff. Existing current-time query retained; tomorrow/evening formats retained.
- Implementation: opt-in compact rendering, title whitespace normalization, inline progress marker, blank line after heading and single line breaks between events; continuation message boundaries preserved.
- Validation: 27 focused agenda tests passed in 1.11s, including exact requested layout, escaped multiline titles and split messages. Docs: 87 links/93 Markdown files, zero missing; diff check passed. Initial test setup needed explicit parent temp directory; fixed fixture dates in assertions to preserve cross-day semantics. No cancelled alternatives. Commit/push pending. No deployment authorization for this new change assumed from the previous release.
- Next: focused tests, docs/diff checks, PR CI and merge. No production mutations.
