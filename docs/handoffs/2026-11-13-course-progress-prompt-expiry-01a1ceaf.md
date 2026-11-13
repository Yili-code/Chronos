# Course progress prompt expiry

- Updated 2026-11-13 18:45 Asia/Taipei, branch codex/course-progress-prompt-expiry, baseline 97318f2 from remote main.
- Scope: the original Telegram course progress question, including operating systems. When the course reply is accepted, SQLite and Firestore persist the prompt ID and a seven-second deadline in the same receipt as the report. The existing expiry handler deletes that message. Replies that leave progress pending, unrecognized or expired replies, and course suggestion cards retain their existing behavior. No bulk cleanup.
- Validation: 4,636 Python tests, including eight new webhook cases across SQLite and Firestore, passed. The cases cover ordinary progress, exam-day reports, acknowledgement-only replies, suggestion cards and duplicate deliveries. All 21 extension tests, documentation links and diff checks passed. Telegram operations were mocked.
- Next: publish the branch, verify CI, and merge. No production rollout authorization was provided.
