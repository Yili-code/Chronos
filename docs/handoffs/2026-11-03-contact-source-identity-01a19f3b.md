# Reject ambiguous mail identities for contacts

- Updated: 2026-11-03 Asia/Taipei. Branch codex/executive-assistant-audit; base aeca9f4.
- Scope: reject ambiguous/multiple/malformed sender identities when creating or resolving a local contact from a mail card, including a source change before the atomic write. No contact sync or external writes.
- Files: contact_relationships.py, targeted regressions and evidence. Validation/commit/CI/deployment pending. Incoming attachment inspection was pushed separately and awaits CI.

## Reproduction and implementation

- Reproduced a contact lookup associating an explicitly ambiguous sender with the existing contact ID. The regression failed before the fix.
- Source resolution now rejects decoder ambiguity, multiple parsed mailboxes, malformed/non-string sender data and invalid native recipient syntax. Creation rechecks source address inside the registry mutation; prepared views recheck before returning a person. Explicit ID-based owner edits remain independent of mail identity guessing.
- Focused mail/contact recipient regressions: 101 passed. Additional first-contact rejection and delayed-view identity changes were included before full-suite execution.
- Ruff, compileall, docs (155 Markdown files / 120 local links / zero missing), diff and source/wheel build checks passed. Full regression pending. No migration or real-recipient write.
- Attachment commit aeca9f457c22809c5e10669fd666c4695f81ac18 CI 41656665479 was in progress at the last check; do not claim it has passed until job results are verified.

## Final local validation

- Full suite: 3,594 passed, zero failures/errors/skips, 142 existing warnings; 181.21 seconds. Built wheel matches source. Evidence: docs/evidence/mail-contact-identity-2026-11-03.json.
- Attachment CI 41656665479 now verified: Python 3.11 / 3.12 and extension all successful. Preparing this identity fix for atomic commit/push; its CI remains pending. No production deployment.
- Wider work remains: group-specific follow-up targets, outgoing and binary attachment handling, multi-party invitations/RSVP, natural multi-intent routing and current 80-case acceptance reporting.
