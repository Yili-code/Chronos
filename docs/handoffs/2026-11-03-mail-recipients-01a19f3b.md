# Reviewed recipient envelopes

- Updated: 2026-11-03 Asia/Taipei. Scope remains single configured Gmail account; LINE/Instagram deferred.
- Branch codex/executive-assistant-audit, base6b6d6c6996136d5c5560682636a64dfd71449609; CI41642641460 completed success, job details to verify before next push.
- Scope: explicit Reply All plus owner-specified To/CC/BCC editing of local drafts, no automatic recipient inference from AI, full envelope preview and fresh version confirmation. Never expand hidden original BCC. Native creation/send use immutable reviewed envelope and verify native recipients.
- Planned files: Gmail decoder/transport helpers, mail_recipients.py, correspondence/send routing, tests/docs. No migration; optional envelope fields.
- Data/control decisions: reject ambiguous headers, duplicate or malformed recipients, capacity overflow and blocked contact preferences. Recipient changes invalidate prior confirmation. CC/BCC remain roles, not flattened into To. Draft remains local until separately confirmed for native save/send.
- Validation/commit/push/deployment pending. All external writes remain mocked; no real-recipient tests. Attachments and multi-party negotiation remain pending beyond this phase.

## Current validation and decisions

- Updated: 2026-11-03 Asia/Taipei. Previous commit CI 41642641460 independently verified: Python 3.11, Python 3.12 and extension jobs all succeeded.
- Implemented explicit recipient roles, Reply All and version-bound local edits; provider MIME and native snapshot checks retain all approved roles. Source changes reset the old envelope instead of carrying stale group recipients forward. Incoming hidden BCC is never exposed or expanded.
- Added trusted receipt-backed buttons. Preparing to send may create the reviewed unsent native draft; a separate short-lived confirmation is still required to send. This intentionally supersedes the initial plan requiring a separate native-save step before preparation. Cancelling invalidates approval; a callback from another card is forbidden.
- Focused validation: 161 passed, including both persistence backends, immutable MIME checks, envelope edits, opt-out changes, webhook card binding and cancelled send consent. Mocked external writes only. An existing assertion was updated to read the structured response text; state and no-write checks remain intact.
- Ruff undefined-name checks passed. Documentation check: 151 Markdown files, 119 links, zero missing. UV lock check and source/wheel build passed using the installed cache after restricted-cache access failed; no dependency changes.
- Full-suite result pending in .pytest-tmp/recipients-full.xml. No commit/push/deployment for this phase yet. Remaining overall scope includes attachment handling, MIME alternative safety, recoverable follow-up reads, group-specific reply tracking, multi-party invitation/RSVP coordination and the final current-state adversarial acceptance report.

## Final local validation

- Full suite after the cancellation race fix: 3,531 passed, zero failures/errors/skips, 142 existing warnings, 177.51 seconds. Earlier pre-fix run: 3,529 passed; newly added race test failed before the fix. The newer result supersedes it.
- Final wheel rebuilt after the fix; six changed production modules match local source exactly. Evidence: docs/evidence/mail-recipients-2026-11-03.json. No dependency or database migration changes.
- Remote main re-fetched: 47cc14215b0c9f774b87efbd96203de9353c7458; no new upstream divergence. Preparing this atomic change for PR publication; CI and production remain separate gates.
