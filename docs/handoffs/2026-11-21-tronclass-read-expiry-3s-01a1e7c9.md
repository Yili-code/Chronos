# TronClass read-notice expiry

- Updated: 2026-11-21T16:51:48+08:00 (Asia/Taipei).
- Scope: retain the existing `已讀` inline button for the no-new-assignment notice and delete that notice three seconds after acknowledgement. Keep Gmail acknowledgement timing unchanged.
- Branch: `codex/tronclass-read-3s`, based on `1964baa254206447552c4cc0bf0f0eb314e15cfc`. The local sync hook failed with `PermissionError`; GitHub's current main head was independently confirmed as this SHA.
- Files changed: `chronos/assignment_scan.py`, `chronos/message_lifecycle.py`, `tests/test_assignment_scan.py`, and this handoff.
- Implemented: parameterized the shared acknowledgement deadline and set only TronClass no-new notices to three seconds. The test asserts the three-second deadline.
- Validation: `git diff --check` and Python syntax compilation passed. Local pytest collection was blocked because no project virtual environment is available and Python 3.14 lacks `apscheduler`. The PR's exact-source CI must provide the required test evidence.
- Not done: PR CI, merge, deployment, and manual Telegram resend. Do not claim release. A repeat scan is idempotent for today's notice and may not resend it; a manual resend must bind its new Telegram message ID to the acknowledgement record.
- Next: verify required CI, merge, deploy the scanner image, and resend the notice with a working bound button.
