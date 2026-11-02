# Exam-day progress and transient completion acknowledgements

- Updated: 2026-11-02 Asia/Taipei. Base a318ee0; branch codex/exam-progress-atomic; isolated worktree.
- Scope: treat exam attendance/no-work reports as no new course progress, close same-day pending suggestions without disabling normal course habits; delete exact completion acknowledgement seven seconds after successful send on callback and command paths.
- Existing unrelated work preserved. Current session deployment authorization retained for this follow-up.
- Diagnosis: habit trigger captures 通常 before exam-day semantics; exact completion notice is not covered by existing seven-second delivery cleanup.
- Pending implementation, full Python tests, appropriate extension/docs checks, PR CI, merge-source CI and guarded deployment. No production task/event/message writes for validation.
- Commit/push: none.

- Implemented exam attendance/no-work report recognition before recurring habit classification; both stores save NO_PROGRESS, close the session and skip only pending proposal items for that day. Already accepted tasks, saved routine preferences and explicit exam preparation/follow-up-work reports remain intact.
- Implemented one shared completion-ack constant and routing through existing awaited seven-second cleanup on callback and command paths. Send failures do not delay/delete; delivered update replay does not resend or restart the timer. This reuses existing best-effort cleanup; process interruption or Telegram deletion failure can still leave a notice.
- Feature verification: 27 tests passed, including both database stores, exact screenshot text, actual webhook branch, pending/accepted work, callback and plain-command cleanup/replay/send failure. Extension 21 passed; docs 98 links, zero missing; compilation/diff passed. Full Python suite running.
- Prepared two atomic commit messages and an index-only patch for the exam-related main.py hunk; all other main.py changes belong to acknowledgement cleanup. Guarded release helper .tmp/exam-progress-release.py prepared but not run.

- Final local validation: 2,999 Python tests passed in 169.63s, one existing Starlette warning. All required local checks complete. Preparing two atomic implementation commits and PR; no deployment yet.

- Publication correction: the first index-only main.py patch lacked a terminal newline and was rejected. A superseding branch codex/exam-progress-atomic preserves the same verified final source while placing the course reply identity/date hunk with its exam change. Original published branch retained; no force push or history rewrite.
