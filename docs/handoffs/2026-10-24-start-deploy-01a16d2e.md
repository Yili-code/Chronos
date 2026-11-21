# Deploy start task list

- Updated: 2026-10-24T17:25:04.406555+08:00
- Authorization: user explicitly requested deployment of /start task list.
- Source: e9add15702ae58a186557b0a028c539994cfc460; exact merge push CI 40081634273 success. Required jobs verified again by release helper.
- Scope: clean git archive; zero-traffic candidate; preserve runtime configuration; readiness/release identity, Calendar read and exact-source webhook regression; unchanged-baseline traffic guard; promotion and final readback.
- Preflight production: chronos-natural-edit-a6a2315 at 100%, source a6a2315ae9c8a39c151347c77f34b69e54bddbdc. Root branch hook current, fetch completed; isolated feature work already merged in PR #62. Existing unrelated modifications preserved.
- Status: deployment in progress; no synthetic production tasks, events or messages planned. Next: verify candidate, promote, record sanitized evidence. Commit/push evidence pending.

- Completed: 2026-10-24T17:30:14.810725+08:00. Production revision chronos-start-list-e9add15 receives 100% traffic. Public ready/Firestore and authenticated internal status confirm e9add15702ae58a186557b0a028c539994cfc460. Calendar read verified; webhook, Calendar and Study configured; Gmail disabled. Exact-source webhook regressions: 3 passed; no production task/event/message writes.
- Concurrency guard: first promotion stopped because another authorized release advanced production to chronos-today-abaaab7. Verified that source is an ancestor of this release and compared actual serving revision configuration/resources/identity/concurrency/timeout. Only CHRONOS_RELEASE_SHA differs. Revalidated and promoted with unchanged-baseline guard. One comparison initially used the service template (already pointing to this candidate); corrected to serving revision without changing runtime settings.
- Rollback: chronos-today-abaaab7. Sanitized evidence: .tmp/start-list-release-e9add15.json. Application deployment complete; evidence publication next. No other-machine synchronization claimed.
