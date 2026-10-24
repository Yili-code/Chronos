# Conversation reply production deployment

- Updated: 2026-10-24 (Asia/Taipei).
- Authorization: explicit user request to deploy PR #53.
- Source: 2afb1156ee467ca6d52bcdf8869ef18e24cdc3a0. Exact merge push CI 40063834058 reports success; helper checks individual required jobs before mutation.
- Baseline: chronos-task-list-68fbca7 at 100% traffic. Target: chronos / asia-east1 / yili-chronos-prod.
- Plan: clean Git archive, preserve runtime configuration, zero-traffic candidate, authenticated readiness, exact-source social reply check, guarded promotion and final public version/traffic readback.
- Status: pending deployment. No task/Calendar mutations or synthetic Telegram messages. Rollback baseline recorded; unrelated local work preserved.
- Commit/push: no deployment record published yet. Next: run guarded release and record evidence; no claim of human indistinguishability or live conversation delivery.

- Completed: 2026-10-24T12:55:07.925377+08:00. Exact merge CI 40063834058 passed Python 3.11, Python 3.12 and extension jobs. Clean-source candidate was verified at zero traffic, then promoted after confirming baseline traffic was unchanged.
- Production: chronos-conversation-2afb115 receives 100% traffic; release 2afb1156ee467ca6d52bcdf8869ef18e24cdc3a0. Public /ready reports ready and Firestore; authenticated /internal/status reports ready, exact release, Telegram webhook/Calendar/Study configured and Gmail disabled.
- Runtime environment and secret references compared with baseline: only CHRONOS_RELEASE_SHA differs. Rollback: chronos-task-list-68fbca7.
- Exact-source offline smoke checks: gratitude response, transparent identity response, no-change acknowledgement and mixed-request passthrough passed. This is not live Telegram end-to-end delivery; no synthetic message, task or Calendar write was performed.
- Local sanitized evidence: .tmp/conversation-release-2afb115.json. Deployment complete; evidence-only PR publication pending. Documentation publication does not require another application deployment.
