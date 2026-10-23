# Plan date options production deployment

- Updated: 2026-10-23T19:23:57.499588+08:00.
- Authorization: explicit user request to deploy PR #42.
- Source: 8bdcb3ef1dc3d34f8834c25d84c8525a24889df0, merge of PR #42. Exact merge-source push CI 39945616245 is still running; deployment waits for every required job.
- Scope: chronos / yili-chronos-prod / asia-east1. Clean Git archive, candidate at zero traffic, verify release/readiness/configuration, guard unchanged baseline traffic, then promote.
- Baseline: chronos-butler-78fd056 receives 100 percent traffic. Preserve environment/secret references except CHRONOS_RELEASE_SHA. No task, Calendar or synthetic Telegram writes for verification.
- Status: preparing release; production unchanged. Source-local tests previously passed 1566 tests and PR CI succeeded; merge-source CI still required.
- Next: wait for merge CI, deploy, verify candidate and public status, publish sanitized evidence through documentation PR.
- Commit/push: deployment record local; unrelated work preserved.

- Completed: 2026-10-23T19:26:53.070937+08:00. Exact merge-source CI 39945616245 passed test (3.11), test (3.12), extension before deployment.
- Production: chronos-plan-dates-8bdcb3e receives 100 percent traffic; release 8bdcb3ef1dc3d34f8834c25d84c8525a24889df0. Clean Git archive deployed at zero traffic, candidate /ready and authenticated /internal/status verified before guarded promotion.
- Public /ready: ready, Firestore, exact release. Internal status: ready, matching release, Telegram webhook configured, learning Calendar configured, Study configured, Gmail disabled.
- Configuration: environment/secret references unchanged except CHRONOS_RELEASE_SHA. Rollback revision: chronos-butler-78fd056. Historical tags retained.
- Evidence: .tmp/plan-dates-release-8bdcb3e.json (sanitized local readback). No task creation, Calendar writes or synthetic Telegram messages used in verification; actual button interaction and seven-second live deletion remain untested.
- Implementation validation: integrated source local suite 1566 passed plus exact merge-source CI. Notice deletion is best effort if Telegram fails or the request process is interrupted.
- Next: publish this deployment record through a documentation-only PR. No additional application deployment needed.
