# Owner message expiry

- Started 2026-11-11 Asia/Taipei on codex/owner-message-expiry at 00bc734. User authorized seven-second removal of their messages in the Chronos chat.
- Scope: authenticated webhook, configured private owner chat, new incoming messages, and existing Telegram expiry. Retain processing and receipts. No historical chat scan or bulk cleanup.
- Successful authenticated webhook deliveries register the configured private chat message with its original Telegram timestamp plus seven seconds. Retry preserves the deadline and deletion tolerates an already-removed message. Group updates, edits, rejected webhooks and unsuccessful processing do not register owner cleanup.
- Local validation: 34 lifecycle tests, 4,621 full Python tests, 21 extension tests, undefined-name, compilation and documentation checks passed. Next: PR CI, main CI and guarded production rollout.
