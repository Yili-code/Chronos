# Bind correspondence to related meeting state

- Updated: 2026-11-04 Asia/Taipei. Branch codex/executive-assistant-audit; base d953f7aae97e39f0cd7f57f8e446c9858a7f77c4.
- Scope: prevent mail drafts bound to a meeting conversation from being saved/sent after cancellation or meaningful calendar changes. Keep mail source fingerprints separate from meeting state so invitation verification cannot invalidate itself.
- Plan: snapshot minimal local meeting fields at drafting, revalidate live owned events before save/send and recheck local snapshot in atomic claims. Preserve uncertain external operations. All regression mutations mocked.
- Implementation, reproduction and validation pending.

- Reproduced: original cancellation-after-preview defect failed both SQLite and Firestore tests (2 failures); after initial binding fix, an additional cancellation during native draft reading also failed both backends (2 failures). Meeting verification now occurs after native draft inspection and local snapshot is rechecked in the atomic send claim.
- Implemented minimal meaningful state fingerprint separate from mail source. Safe meeting details ground draft generation; internal codes/account bindings are omitted from AI payload. Save and send revalidate live owned holds/invitations. Unknown reads stop submission. Legacy drafts cannot bypass a newly related meeting. Bookkeeping changes do not invalidate consent.
- Focused integration/correspondence validation: 202 passed. Provider writes mocked. Complete regression/build pending; no migration, no merge/deploy.
- Remaining: cross-provider changes after the final read cannot be atomically excluded; Gmail/Calendar do not share a transaction. Unrelated threads are not inferred as the same meeting. Stale native Gmail drafts remain preserved for owner inspection, not silently rewritten or deleted.

- Prior conversion publication confirmed: d953f7aae97e39f0cd7f57f8e446c9858a7f77c4. CI 41780226591 Python 3.11, Python 3.12 and extension all succeeded. This does not establish production deployment.

- Final validation: 3,854 passed, zero failures/errors/skips, 142 existing warnings; 190.93 seconds wall output. Release wheel matches three changed/new runtime modules. Ruff, docs links and diff checks passed. Evidence: docs/evidence/mail-meeting-binding-2026-11-04.json. Commit/push and CI pending at this entry; no merge/deployment.
- Next implementation priorities: reviewed rescheduling, bounded mail follow-up polling, explicit monitoring lifecycle, broader natural intent routing and current 80-case evidence. Existing registry cleanup must treat restored held conversions as inactive without dropping in-flight invitation claims. No claim these are completed.
