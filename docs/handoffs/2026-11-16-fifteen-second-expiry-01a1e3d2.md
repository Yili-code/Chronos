# Fifteen-second Telegram message expiry

- Started 2026-11-16 Asia/Taipei from d664621 on codex/fifteen-second-expiry. User authorized deployment after the change.
- Scope: every Chronos expiry previously set to seven seconds, including shared passive notices, owner requests, read acknowledgements, course cards, and explicit mail and task-review results.
- Keep stored absolute deadlines intact. Map old seven-second relative hints in saved delivery receipts to the current policy when replayed. Other durations and pending-interaction rules retain their existing behavior.

- Updated 2026-11-16T21:09:12.7824379+08:00. Shared delay and explicit mail and review delays now use 15 seconds. Updated user documentation and timer assertions. Documentation and diff checks passed. Full local validation restarted with a shorter temporary path after a Windows permission error in an unrelated Gmail authorization test. Next: push after full validation, wait for PR and merge CI, deploy the verified merge and check traffic and service status.
