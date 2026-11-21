# Planning card completion

- Started 2026-11-11 Asia/Taipei on codex/planning-card-completion, based on 3f71e28.
- Screenshot shows a direct reply to a no-slot proposal routed to course feedback. Proposal delivery already records the exact Telegram message ID. The webhook skips that binding and falls through to the course handler. Existing unbooked completion handles only proposed/editing plans and recognizes short declarations.
- Scope: chronos/main.py, learning_calendar.py, SQLite/Firestore fallback wording, and focused webhook regression tests. Preserve unrelated root edits. User authorized testing and production rollout.

- Implemented exact active-proposal message matching, deterministic full-completion wording, no-slot completion, and neutral fallback guidance. Completion shares the update receipt transaction, checks the same proposal and live task snapshot, and releases the active turn for subsequent planning.
- Focused validation: 16 passed including exact screenshot wording, partial progress and stale message IDs on SQLite and Firestore, webhook replay, and absence of Calendar writes. Extension suite (21), undefined-name checks, compilation, 129 local documentation links and diff checks passed. Full local Python suite passed: 4,614 tests in 186.92 seconds with 141 warnings. Next: branch push, PR CI, merge CI, then guarded deployment.
