# Telegram task list expiry

- Updated 2026-11-12 15:43 Asia/Taipei. Branch codex/task-list-expiry, baseline 5033099 verified against remote main.
- Scope: Telegram bot replies to /task, the existing message expiry scheduler, and webhook regression coverage. The original working directory contains unrelated handoff changes and is isolated from this branch.
- Implemented a 30-second lifetime for /task after a successful delivery. The scheduler persists the requested deadline using its existing expiry store. The default remains seven seconds and explicit reply requirements retain precedence.
- Local verification: all six new webhook cases passed, covering /task, /tasks and /start with empty and populated lists, deletion of the delivered message, and preserved task records. Documentation links and diff checks passed. The first run could not create the temporary directory. After creating it and pointing TEMP and TMP at it, the full suite passed: 4,627 tests in 197.19 seconds, with one dependency warning.
- Next: push the isolated change and merge only after required PR checks pass. Production rollout requires separate authorization.
