# Reviewed Calendar invitations and RSVP evidence

- Updated: 2026-11-03 Asia/Taipei. Branch codex/executive-assistant-audit; base 3acea0cd4764d6087b55760d495af8510feedbaa. Remote main re-fetched, remains 47cc14215b0c9f774b87efbd96203de9353c7458.
- Scope: explicit reviewed recipient/location/time invitation submission and read-only RSVP reconciliation. Keep private holds distinct from submitted invitations and all-invitee acceptance. No real-recipient test messages or Calendar writes.
- Planned files: meeting_invitations.py, existing meeting routing/state guards, Google Calendar transport and tests. No schema migration.
- Safety contract: account/card/source/version binding, explicit attendee list, contact opt-out checks, owner-calendar recheck, independent expiring approval, immutable payload, deterministic event ID, no retry after unknown writes, verified remote ownership/content before reconciliation or cancellation. Event status confirmed alone does not establish attendee agreement. Unsupported or incomplete provider evidence remains unverified.
- Initial phase: invitation from a reviewed candidate, RSVP state, explicit reviewed cancellation. Conversion of existing private holds, rescheduling, automatic polling and natural multi-intent routing remain separate work until implemented/tested. Do not claim full coordination acceptance from this phase.
- Official contracts consulted: https://developers.google.com/workspace/calendar/api/v3/reference/events/insert and events resource. Existing Calendar transport has safe errors but currently no structured HTTP status; preserve old callers while adding that optional diagnostic.
- Validation/implementation/commit/CI/deployment pending. Follow-up target commit 3acea0c was pushed; CI pending.

## Implementation and reproduced defects

- Added explicit attendee/location preview, separate expiring consent, exact Calendar/account/source binding, opt-out checks during the atomic claim, deterministic immutable event creation with notifications to all guests, and strict RSVP reduction. The organizer status alone never proves all guests accepted.
- Added independent cancellation review with fresh owned-event/content checks, ETag and unique claim. Unknown write outcomes are not replayed. Read-only lookup can recover a matching created event; cancellation uncertainty remains conservative. Local review can be abandoned explicitly.
- Calendar transport gains optional HTTP status on safe CalendarError and separate explicit notification methods; existing private-hold defaults stay sendUpdates=none.
- Initial invitation test failed because the capability was absent. First full run passed 3,678 tests, but subsequent review reproduced a scope defect: a single event externally converted to a recurring series was still eligible for cancellation review. The new regression failed before the fix. Ownership validation now rejects recurrence/instances, private copies, changed event types/guest permissions, missing ETags, omitted/extra guests and fake organizer entries. Malformed RSVP values stay unverified.
- Focused progression: 118 existing/new integration/transport tests passed before the scope discovery; final invitation-only regression run: 74 passed. A fresh full run after the scope fix is pending, superseding the earlier full run as the release gate.
- Ruff, compileall, docs and diff checks passed; final source/wheel rebuild pending completion. All network writes are mocked. No migration or production deployment.
- Follow-up target commit 3acea0cd4764d6087b55760d495af8510feedbaa CI 41659472422 verified: Python 3.11 / 3.12 and extension all successful.

## Final local validation

- Full suite after the scope fix: 3,696 passed, zero failures/errors/skips; 142 existing warnings; 179.46 seconds. This supersedes the pre-scope-fix full run. Final wheel matches all five changed/new production modules. Evidence: docs/evidence/meeting-invitations-2026-11-03.json.
- Preparing atomic commit/push to draft PR 124; this invitation change still requires its own CI. No production deployment, real invitations or calendar writes.
- Next priority: reliable automatic RSVP indexing/polling, then existing-hold conversion and reviewed rescheduling. Broader remaining work includes binary/outgoing attachments, natural multi-intent routing and a current 80-case acceptance report. Historical baseline results must not be relabeled as passing.
