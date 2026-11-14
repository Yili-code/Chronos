# Invitation card layout

- Started 2026-11-14 Asia/Taipei from a991280 on codex/invitation-card-layout. Scope: show weekdays on invitation dates, consolidate repeated logistics, keep unrelated mail facts and existing actions. Previous live Epoch School Party resend established the rendering issue. No resend or deployment requested in this turn.

- Updated 2026-11-14 23:09 Asia/Taipei. Added Chinese weekdays to the schedule header and next-day section. Removed the repeated current date from the agenda label and title. Filtered repeated logistics clauses only when matching fields are visible elsewhere, keeping independent facts and keeping the venue when no route is rendered. Covered normal delivery and bound schedule follow-ups. Full suite 4,656 passed, 57 focused tests passed, docs and diff checks passed. Next: push PR and merge after CI. Production and prior card unchanged.
