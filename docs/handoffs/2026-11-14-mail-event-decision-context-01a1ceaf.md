# Mail event decision context

- Updated 2026-11-14 Asia/Taipei on codex/mail-event-decision-context, baseline f588f6a from remote main.
- Individual optional Gmail cards now show invitation time, fixed or unverified overlapping commitments, adjustable overlapping items and their tradeoff, and chronological agendas for the event day and following day. Busy-only calendar windows count as conflicts. Long agendas show five rows and a day link.
- Invitation time is extracted during screening and validated against email sources. If timing or Calendar data cannot be confirmed, the card states what is unknown. Calendar is read only, and schedule data is not sent to the model. Existing invitations, delivery receipts, and mail actions use their existing paths.
- Validation: 4,648 Python tests passed, including 12 new cases across both persistence backends, and 21 extension tests passed. Documentation links, compilation and diff checks passed. Calendar and Telegram were mocked in the new regressions.
- Next: publish and merge after CI. No manual mail processing, production push, or deployment.
