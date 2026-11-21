# Event followup production deployment

- Updated: 2026-10-23T16:45:30+08:00.
- Authorization: user explicitly requested deployment of the event clarification fix.
- Source: merged 278d6e74f338b472e32e6a6adfa3111b49b33871; exact push CI 39926655214 completed successfully (individual jobs are checked by the release helper before deployment).
- Scope: clean Git archive to yili-chronos-prod / asia-east1 / chronos. Preserve runtime configuration, check production ancestry, deploy with no traffic, verify candidate, guard against concurrent traffic changes, promote and verify public release.
- No Calendar/task repair or Telegram test messages. Live provider checks will be read-only interpretation calls.
- Status: deployment pending; no commit/push yet. Baseline and evidence will be recorded after verification.

- Update 2026-10-23T16:51:08+08:00: live provider gate rejected 278d6e7; stopped promotion. Readback confirms chronos-exam-3892735 still at 100%, candidate at zero. Root cause is a second EventDetails extraction call receiving only current message; correction PR #32 (072eb81), full suite 1,495 passed and live working-source two-turn scenario passed. Candidate release helper now runs the exact-source semantic gate before promotion. PR CI pending.

- Completed 2026-10-23T16:56:10+08:00: correction PR #32 passed all required checks and merged as 2b3efd32852ed0e6a109eeb7e0a5c8efee67f207. Exact merged push CI 39927677375 passed Python 3.11, Python 3.12 and extension. Clean archive deployed as chronos-followup-2b3efd3, receiving 100% traffic. Public /ready confirms ready / Firestore / exact release; authenticated status confirms same release and configured Telegram webhook, Calendar and Study; Gmail disabled.
- Configuration differs only in CHRONOS_RELEASE_SHA. Concurrent cleanup release had completed first: ancestry was verified and rollback baseline is chronos-cleanup-79e8086. Current traffic was checked before promotion.
- Exact deployed-source live Gemini check passed: missing end asks clarification, 到十一點 resumes exam_create semantics as event_create/exam for 2026-10-30 09:20-11:00; 查明天行程 selects agenda. Sampled provider checks do not establish general accuracy or Telegram end-to-end delivery.
- Sanitized local evidence: .tmp/followup-release-2b3efd3.json and .tmp/followup-live-check.json. No task, Calendar or synthetic Telegram writes. No existing data repair. The zero-traffic 278d6e7 candidate remains unpromoted.
- Deployment complete. This record is being published through a documentation-only PR; no further application deployment is required for the record.
