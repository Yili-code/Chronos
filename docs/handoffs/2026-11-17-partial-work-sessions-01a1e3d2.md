# Partial work-session approval

- Started 2026-11-17 Asia/Taipei on codex/partial-work-sessions from 54c81ea. When the original window fits only part of the requested effort, show the exact blocks and permit explicit approval. Preserve the remaining minutes for the same task.
- Scope: split preview and booking, schedule state, final partial feedback, and tests. Production deployment requires separate authorization.

- Updated 2026-11-17T11:43:55.5281359+08:00. Preview shows the exact blocks, bookable total and remainder, including a single available block. The confirmation card uses a fresh token so an old card cannot approve revised blocks. The existing recoverable booking operation persists the remainder on the original task and /plan displays it. Partial feedback after the final reserved block queues the remaining estimate for the same task; partial feedback before that point leaves the planned blocks available.
- Focused tests passed on SQLite and Firestore (14 cases), including 165/180, single-block shortage, zero capacity, stale cards, changed availability, uncertain insert/retry and same-task remainder. Documentation and diff checks passed. Full suite underway.
