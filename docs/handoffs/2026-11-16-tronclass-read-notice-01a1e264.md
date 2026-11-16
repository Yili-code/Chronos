# TronClass empty scan acknowledgement

- Started 2026-11-16 Asia/Taipei from `73670ed`, on `codex/tronclass-read-notice` in an isolated worktree. Scope is the scheduled no-new-assignments notification and its plain-text acknowledgement button.
- Reuse the scan delivery record to bind the button and preserve the first click timestamp. Reuse Telegram message expiry for the delayed deletion. Validation, publication and CI pending.
- Follow-up extended the same button to Gmail screening digest pages categorized as system notifications or product and service messages. Each button is bound to its sent Telegram message and preserves its first click across webhook retries. It does not mark mail as read in Gmail. The combined change is awaiting full local validation.
- Local verification passed: 110 targeted and 4,667 full Python tests, documentation and diff checks. No live Telegram or Gmail activity was performed. Publish the atomic change through a PR and merge after required CI. Deployment is separate.
