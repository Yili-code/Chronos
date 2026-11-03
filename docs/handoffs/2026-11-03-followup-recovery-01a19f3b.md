# Recoverable follow-up reads

- Updated: 2026-11-03 Asia/Taipei. Branch codex/executive-assistant-audit, base 9a5ee03. Scope: distinguish read-only verification failures from uncertain notification writes and check current baseline/deadline inside reminder claims.
- Files: mail_followups.py, regression tests and correspondence documentation. No schema migration, no real-recipient writes. Initial behavior permanently pauses a wait after a transient read failure. Validation/commit/CI/deployment pending.

## Reproductions and implementation

- Reproduced transient read failure changing waiting_other to permanently paused. Reproduced a deadline changed to the future during provider read still generating a stale reminder; both tests failed before the fix.
- Read-only errors now preserve wait/version/deadline and set read_unverified. Status discloses verification failure. A fresh successful read clears the flag and can resolve or remind; delivery ambiguity remains paused with no replay. Legacy ambiguous paused records are not automatically resumed.
- Atomic notification claim now verifies the current waiting status, verification flag, baseline and deadline, rather than only version/active status.
- Focused regression: 94 passed; additional baseline-change, outage-cancellation and repeated-permission-failure tests were added before the full suite. Initial command referenced a nonexistent integration test filename and ran no tests; corrected to existing files. No result claimed for the invalid command.
- Ruff, compileall, docs (153 Markdown files / 119 links / zero missing), diff check and source/wheel build passed. Full-suite result pending.
- MIME commit 9a5ee030aa5335ce02cf59f44d37f19c3feaebb3 CI 41654198620 independently verified: Python 3.11 / 3.12 and extension all successful. No production deployment.

## Final local validation

- Full suite: 3,556 passed, zero failures/errors/skips, 142 existing warnings; 176.95 seconds. Built wheel matches changed production source. Evidence: docs/evidence/mail-followup-recovery-2026-11-03.json.
- Preparing atomic commit/push to PR 124. CI remains pending for this phase; no deployment. Overall acceptance work remains incomplete: attachment workflows, group-specific follow-up targets, true invitation/RSVP coordination and current 80-case acceptance evidence.
