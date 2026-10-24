# Natural-language booking edit production deployment

- Updated: 2026-10-24 (Asia/Taipei).
- Authorization: explicit user request to deploy PR #59.
- Source: a6a2315ae9c8a39c151347c77f34b69e54bddbdc. Exact merge push CI 40071812360 succeeded; helper verifies all required jobs before release.
- Scope: guarded clean-source release of bounded natural-language booked edits and exact starts. Preserve current automatic acceptance and refreshed Calendar secret bindings. No synthetic production task, Calendar event or Telegram message writes.
- Plan: zero-traffic candidate, source/readiness/runtime configuration and actual Calendar-read verification, exact-source synthetic route regressions, unchanged-baseline traffic guard, promotion and final public readback.
- Status: preparing deployment. Rollback revision recorded by helper. No new credential publication or rotation requested or required.
- Commit/push: deployment evidence pending. Next: complete runtime checks, publish sanitized evidence-only PR.

- Completed 2026-10-24T14:55:59.327806+08:00: exact-source push CI 40071812360 passed Python 3.11, Python 3.12 and extension. Clean-source candidate verified at zero traffic, then promoted after unchanged-baseline traffic check.
- Production: chronos-natural-edit-a6a2315 receives 100% traffic; application source a6a2315ae9c8a39c151347c77f34b69e54bddbdc. Public ready/Firestore and authenticated internal readiness match the release; webhook/Calendar/Study configured, Gmail disabled.
- Actual Calendar read verified on candidate. Runtime env and pinned secret references unchanged except CHRONOS_RELEASE_SHA; automatic acceptance remains true and refreshed Calendar grant preserved. Runtime identity, concurrency, timeout and resource limits compared with baseline and unchanged.
- Exact-source synthetic webhook/edit tests: 30 passed in 1.85 seconds, including all ten live-output cases on both backends and invalid/occupied-window tests. Existing Starlette deprecation warning only. No synthetic production message, task or Calendar write performed; no live Telegram editing interaction claimed.
- Rollback: chronos-autoplan-auth-142442. Sanitized local release evidence: .tmp/natural-edit-release-a6a2315.json.
- Deployment complete. Next: publish sanitized evidence through documentation-only PR; no further application deployment needed for that record. Other computers not verified synchronized.
