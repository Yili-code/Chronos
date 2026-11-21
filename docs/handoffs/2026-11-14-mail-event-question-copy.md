# Mail event clarification wording

- Started 2026-11-14 Asia/Taipei on codex/mail-event-question-copy at f588f6a. Scope is the mail Task callback and its Telegram clarification.
- The missing-time branch forwards model-generated question text verbatim. Use application-owned Traditional Chinese wording and preserve the reply binding and existing expiry. Keep the optional field for compatibility with saved plans and provider responses.
- Updated 2026-11-14T12:21:27.7108590+08:00. Full Python suite: 4,636 passed. The webhook regression checked the exact Telegram message despite malformed model text, reply binding, and the existing 60-second expiry. Documentation and diff checks passed. Next: push, open PR, verify CI, and merge. Production delivery was not exercised.
