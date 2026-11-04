# Bound attachment inspection

- Updated: 2026-11-03 Asia/Taipei. Branch codex/executive-assistant-audit, base 131c0efcfc907d93e4295cdc10cc6b8e5ed53023.
- Scope: incoming attachment inventory and explicit bounded read through official Gmail API, bound to the selected owner/card/message. No arbitrary URLs/filesystem access, no execution, no automatic attachment forwarding. No claim that unsupported binary formats were read.
- Planned files: gmail.py, mail_attachments.py, mail_workflow.py, explicit command routing and regressions. No database migration; stored review contains bounded text/hash, not raw binary. Validation and implementation pending.

## Implemented inspection boundary

- Added bounded incoming part metadata (no raw attachment IDs/content exposed in inventory), paginated private lists and fresh source/version checks. Service reads through the official Gmail API, not arbitrary user URLs or paths.
- In-memory transport rejects invalid part paths, provider IDs, base64, declared/actual size mismatches and files exceeding 1 MiB. Owner previews allow only UTF-8 text/plain, text/csv and application/json within 2,800 UTF-16 units; malformed/control/binary content is not saved as understood. No executable or file opening.
- Stored reviews contain bounded text plus SHA-256. Card responses are rebound to the originating mail; unbound commands remain excluded from generic task creation. Existing incomplete-context drafting guards remain intact. No outgoing attachments, PDF/image/Office extraction or whole-thread-understanding claim.
- First inventory regression failed before implementation. Initial service tests required fixing fixture binding and clearing an inherited read side effect; actual focused run: 22 passed, including real webhook routing and both databases. Additional MIME compatibility run: 40 passed. Static checks, compileall, docs (154 Markdown / 119 links / zero missing), diff check passed. Full suite/build pending.
- Overall follow-up commit 131c0ef was pushed; its CI must be checked before next publication. No production deployment or real-recipient writes.

## Final local validation

- Full suite: 3,578 passed, zero failures/errors/skips; 142 existing warnings; 188.83 seconds. Final wheel matches all five changed production modules. Evidence: docs/evidence/mail-attachment-inspection-2026-11-03.json.
- Follow-up commit CI 41655223985 verified: Python 3.11 / 3.12 and extension successful. This attachment change is preparing for atomic commit/push; its CI remains pending. No deployment.
- Next safety check discovered during review: local contact source_address does not yet reject the decoder sender_ambiguous flag. Address before release with a reproducing regression. Wider acceptance still includes outgoing attachments/PDF handling, group-specific tracking, multi-party invitations/RSVP and current 80-case evaluation.
