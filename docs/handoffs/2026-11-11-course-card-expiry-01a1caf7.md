# Course suggestion card expiry

- Started 2026-11-11 Asia/Taipei on codex/course-card-expiry at 890445c.
- Retain the shared course card until its final accept/skip decision. Save the seven-second deadline with the receipt and use the existing Telegram expiry mechanism. Scope: course_habits, webhook and focused regression. No production rollout authorization for this change yet.
- Validation: 16 course-habit tests, 4,614 full Python tests, 21 extension tests, static checks and 129 documentation links passed. Webhook test confirms the first decision preserves the card, the final decision schedules the exact original ID for seven seconds, and replay does not delete again.
- Telegram Bot API deleteMessage is limited to messages sent less than 48 hours ago. Older cards may remain. Next: PR and CI. Production deployment awaits separate authorization.
