# Explicit follow-up identities

- Updated: 2026-11-03 Asia/Taipei. Branch codex/executive-assistant-audit; base b409fa1. Scope: exact recipient-bound waits within group email, refusing ambiguous target selection. No claim of tracking every group participant or inferred alias identity.
- Planned files: mail_followups.py and synthetic fixtures/regressions. One target per bound thread; explicit recipient selection required for multi-recipient outgoing messages. No external writes or migration.
- Validation, commit, CI, deployment pending. Prior contact identity change was pushed separately.

## Reproduction and implementation

- Reproduced a multi-recipient outgoing message creating an unspecified wait; first regression failed before the fix.
- Waits now bind one exact visible email target. Multi-recipient mail requires explicit selection; full history chooses the latest relevant interaction when another participant replied later. Unrelated human/automatic replies do not resolve or pause that target. Owner replies require recipient evidence, and unrelated outgoing mail does not reset the deadline.
- Active targets cannot be silently replaced. Legacy records without target pause until explicitly re-established. Reminder claims verify target as well as baseline/deadline/status. Recipient/sender metadata failures remain read-verification failures; only actual notification writes enter delivery uncertainty.
- Focused run: 76 passed. Existing outgoing fixture now appends an actual SENT message with To metadata instead of changing an incoming message's sender; owner-response integration fixture now includes its recipient. Assertions for no external send, resolution and stale-state protection were retained.
- Ruff, compileall, docs (156 Markdown / 120 local links / zero missing), diff check and source/wheel build passed. Full suite pending. No deployment.
- Contact identity commit b409fa14eace7f342b144ef9daf60627854ccb19 CI 41657706027 overall success; job-level details checked separately before publication.

## Final local validation

- Full suite: 3,622 passed, zero failures/errors/skips; 142 existing warnings; 178.22 seconds. Built wheel matches source. Evidence: docs/evidence/mail-followup-targets-2026-11-03.json.
- Contact identity CI 41657706027 job-level results verified: Python 3.11 / 3.12 and extension all successful. Preparing this target change for atomic commit/push; its own CI remains pending. No deployment.
- Next implementation priority: real reviewed Calendar invitations and RSVP status, retaining the distinction between tentative/private holds, submitted invitations and all-invitee acceptance. Other remaining scope includes binary/outgoing attachments, natural multi-intent routing and a current 80-case acceptance report.
