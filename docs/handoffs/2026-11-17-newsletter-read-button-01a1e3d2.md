# Read buttons on every Gmail digest page

- Started 2026-11-17 Asia/Taipei from 491440e on codex/newsletter-read-button. Independent clone synced successfully, leaving the original checkout lock untouched.
- Remove the category-specific acknowledgement rule. Every screening digest page, including newsletters and resumed pages, uses the existing message-bound Read button and 15-second cleanup. Individual mail action cards preserve their separate controls.
- Validate fresh and resumed digest delivery, message binding, and repeat clicks. Production deployment has not been requested.

- Updated 2026-11-17T12:56:28.1452065+08:00. All six digest categories and saved unsent pages use one delivery policy. Regression checks cover every category, older snapshots without category metadata, the original click deadline, and newsletter deletion through the webhook with no Gmail mark-read call. Focused suite 154 passed plus one separately run webhook case; documentation and diff checks passed. Full local test run in progress.
