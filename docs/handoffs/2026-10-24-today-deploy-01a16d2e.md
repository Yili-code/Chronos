# Today remaining agenda deployment

- Updated: 2026-10-24 (Asia/Taipei).
- Authorization: explicit user request to deploy /today change.
- Source: abaaab72e0237a8b1eeb1bac08c1fe3d7f8d4388, PR #61; exact merge push CI 40081347270 succeeded; deployment helper rechecks required jobs.
- Parent branch codex/production-ack-evidence contains unrelated local handoffs; deploy uses clean git archive of exact merge source. No parent edits included.
- Scope: zero-traffic candidate, exact-source agenda regressions, public/authenticated readiness, actual Calendar read, environment/secret/runtime configuration comparison, unchanged-baseline traffic guard, promotion and public readback.
- Status: preparing. No real Calendar/task modifications or synthetic Telegram message. No cancelled alternatives.
- Commit/push: application merged; deployment evidence pending. Next: verify candidate and promote; record sanitized result.

- Completed 2026-10-24T17:27:23.224303+08:00: revision chronos-today-abaaab7 receives 100% production traffic; public and internal readiness match abaaab72e0237a8b1eeb1bac08c1fe3d7f8d4388; Firestore ready, webhook/Calendar/Study configured, Gmail disabled.
- Actual candidate Calendar read verified; 25 exact-source agenda tests passed in 1.27s (existing Starlette deprecation warning). Query boundaries covered with mocked Calendar transport; no live Telegram /today E2E claimed.
- Runtime identity, concurrency, timeout, resources, ports, command/args, environment and secret references compared with baseline; only release SHA changed. Auto acceptance preserved. Promotion required baseline traffic to remain unchanged.
- Rollback: chronos-natural-edit-a6a2315. Sanitized local evidence: .tmp/today-release-abaaab7.json.
- Application deployment complete. Next: publish this sanitized record through documentation-only PR; no additional application deployment needed. Other computers not verified synchronized.
