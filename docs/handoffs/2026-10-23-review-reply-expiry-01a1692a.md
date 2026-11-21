# Review result expiry

- Updated: 2026-10-23 (Asia/Taipei).
- Base: 0e839a5; branch codex/review-reply-expiry, isolated worktree.
- Sync verified; parent uncommitted records preserved.
- Scope: screenshot task-review apply/cancel result replies expire after seven seconds; Undo remains available during that interval. Pending previews and errors remain visible.
- Decision: reuse existing awaited seven-second cleanup in delivery, with explicit receipt metadata rather than matching translated text. No detached background timer on scale-to-zero runtime.
- Implemented: explicit delete_after_seconds=7 on successful review apply/cancel receipts, consumed by existing delivery cleanup. No wording matching for review results; no effect on pending previews or errors.
- Validation: full Python suite 1,583 passed in 114.84s; focused review suite 59 passed; documentation 86 local links across 73 Markdown files, zero missing; diff check passed. Existing Starlette deprecation warning. First full attempt stopped because basetemp parent did not exist; created it and reran complete suite successfully.
- Limits: cleanup uses existing awaited delay and best-effort deletion; process termination or Telegram deletion failure can leave a message visible. No durable deletion queue introduced. Existing historical messages are unaffected.
- Next: commit/push, PR CI, merge after required checks. No production deployment authorized. Commit/push pending.
