# Review reply expiry production deployment

- Updated: 2026-10-23 (Asia/Taipei).
- Authorization: user explicitly requested deployment after PR #46 merged.
- Target: 555bf651a0f8e44fffe2c546ebd0bfec80953a52; exact merge CI 39971165005 succeeded; helper verifies all required jobs before deploying.
- Strategy: clean Git archive, zero-traffic candidate, readiness and exact version, preserve runtime configuration, ancestry and unchanged-traffic guard, then promote and verify.
- Scope: only deployment and read-only verification. No synthetic Telegram/task/Calendar changes; old messages remain unaffected.
- Status: preparing deployment. Commit/push of evidence pending; unrelated parent modifications preserved.
- Next: candidate deployment, promotion/readback, publish sanitized evidence.

- Completed: 2026-10-23T22:42:32.418669+08:00.
- Production: chronos-review-expiry-555bf65 receives 100 percent traffic. Public /ready and authenticated /internal/status confirm ready and exact release 555bf651a0f8e44fffe2c546ebd0bfec80953a52, Firestore ready, Telegram webhook configured.
- Candidate validated before promotion; environment and secret references unchanged except CHRONOS_RELEASE_SHA. Baseline ancestry and unchanged traffic verified before promotion.
- Exact merge CI 39971165005: Python 3.11, Python 3.12 and extension all succeeded.
- Rollback: chronos-plan-summary-0e839a5. Sanitized local evidence: .tmp/review-expiry-release-555bf65.json.
- Validation boundary: release, health, runtime configuration and traffic verified live. No synthetic Telegram send/callback or task mutation; seven-second deletion behavior is covered by integration mocks. Historical messages unchanged.
- Runtime work complete. Publishing evidence via an isolated documentation PR; other computers not verified synchronized.
