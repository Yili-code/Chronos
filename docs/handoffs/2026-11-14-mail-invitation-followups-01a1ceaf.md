# Invitation travel and follow-up questions

- Started 2026-11-14 Asia/Taipei on codex/mail-invitation-followups, baseline 2c8bdbf. Scope: actual overlap windows, Google Maps travel context, and read-only questions on bound Telegram invitation cards. Existing unrelated work stays in its original worktree.

- Updated 2026-11-14 12:36 Asia/Taipei. Implemented exact overlap timestamps, transit directions and optional Google Routes travel estimates. Use the nearest preceding event's physical location or the configured default (奇浩聚會所). Show a fallback reason when needed. Validated invitation venue text against the source. No real Maps requests or billing changes.
- Bound invitation replies recognize supported agenda and travel questions and offer read-only rescheduling candidates for a named conflict. Preserve meeting length, avoid known busy windows and the invitation, and reserve estimated inbound travel. Availability is owner-only. Existing explicit mail actions keep their existing routes.
- Validation: 4,652 Python and 21 extension tests passed. Docs, compilation, and diff checks passed. Mocked Calendar, Telegram, and Google Routes. Next: push PR, confirm CI, and merge. Production rollout and Maps API key provisioning remain pending.
