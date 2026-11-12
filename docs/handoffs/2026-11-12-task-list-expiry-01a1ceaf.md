# Telegram task list expiry

- Updated 2026-11-12 15:43 Asia/Taipei. Branch codex/task-list-expiry, baseline 5033099 verified against remote main.
- Scope: Telegram bot replies to /task, the existing message expiry scheduler, and webhook regression coverage. The original working directory contains unrelated handoff changes and is isolated from this branch.
- Implemented a 30-second lifetime for /task after a successful delivery. The scheduler persists the requested deadline using its existing expiry store. The default remains seven seconds and explicit reply requirements retain precedence.
- Local verification: all six new webhook cases passed, covering /task, /tasks and /start with empty and populated lists, deletion of the delivered message, and preserved task records. Documentation links and diff checks passed. The first run could not create the temporary directory. After creating it and pointing TEMP and TMP at it, the full suite passed: 4,627 tests in 197.19 seconds, with one dependency warning.
- Next: push the isolated change and merge only after required PR checks pass. Production rollout requires separate authorization.

- Updated 2026-11-12 15:49 Asia/Taipei. Published the initial commit and opened PR #167. Main moved to da9ff80 with the shared duration support in PR #166. Integrated that version of the scheduler and retained only the /task override, webhook regressions, and this handoff in the diff against main.
- Integrated validation: the first Windows run hit an access error while replacing a Gmail fixture file. A clean rerun passed all 4,628 Python tests in 184.70 seconds, with one dependency warning. All 21 extension tests passed. Documentation link and diff checks passed. Next: push the integrated branch and verify the exact PR head in CI.
