# Calendar intent routing analysis

- Updated: 2026-10-21 10:19 +08:00 (Asia/Taipei).
- Branch/base: main / e4474d1. Startup hook reported current history and clean tree.
- Scope: analyze evening window rejection and unintended task creation; no application changes or deployment requested.
- Reviewed: chronos/calendar_replan.py, chronos/main.py, chronos/ai.py, chronos/learning_calendar.py, chronos/task_timing.py, tests/test_calendar_replan.py and existing handoffs. No other active handoff found.
- Findings: explicit_window recognizes afternoon and now-to-clock requests, but not evening-only requests. These fall through to parse_replan, whose prompt rejects ambiguous times without defining evening. preview combines AI failures, non-actionable output and invalid timestamps into the same generic response; the observed response cannot identify the exact model result.
- Findings: Telegram replan routing requires /replan, a matching callback, or an active editing session. Ordinary unbound text falls through to ai.parse and task creation. Time uncertainty is stored on the created task, not used as a pre-creation intent confirmation.
- Verification: isolated direct calls using .venv Python at a synthetic 2026-10-20 15:33 +08:00 returned None for an evening-only request; afternoon returned 15:33–18:00; explicit now-to-21:00 returned 15:33–21:00. Initial system Python lacked httpx; reran successfully in .venv. No live AI, Telegram, Calendar or private database access; production revision unknown.
- Decision: analysis only. Recommend shared evening semantics, pre-creation schedule-intent routing, and distinct failure diagnostics. Freeing a window should preview moves with Allow, not delete tasks. Evening end boundary needs an explicit product policy; existing study-window 18:00–21:00 is not proof that all evening events end at 21:00.
- Unfinished/next: implement and test only if requested; include both entry paths, expired windows, immovable events and confirmation behavior. No abandoned implementation.
- Commit/push: this documentation is the only task change; commit and push attempted after review. Verify Git tracking state for publication result.
