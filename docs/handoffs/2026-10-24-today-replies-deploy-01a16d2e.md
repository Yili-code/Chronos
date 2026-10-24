# Today agenda replies production deployment

- Updated: 2026-10-24T18:15:10.154348+08:00.
- Authorization: explicit user request to deploy the completed today-list audit fixes.
- Source: a1915b525f6c883c1a43f45c1882d8253a16b2e0, PR #66; exact merge push CI 40084380459 succeeded. Helper rechecks all required jobs.
- Parent branch codex/production-ack-evidence has unrelated local handoffs; deploy uses exact clean git archive. No local work included.
- Scope: zero-traffic candidate, exact-source agenda/response regression replay, real Calendar read, public/internal readiness and release checks; preserve active production environment, secret references and runtime limits; guard unchanged traffic before promotion.
- Status: preparing. No task/event mutation or live synthetic Telegram send intended. No cancelled alternatives.
- Commit/push: application shared in main; deployment evidence pending. Next: validate candidate, promote, verify final traffic and publish sanitized receipt.

- Completed 2026-10-24T18:17:46.894667+08:00: chronos-today-replies-a1915b5 receives 100% traffic, application source a1915b525f6c883c1a43f45c1882d8253a16b2e0. Public and authenticated readiness match source; Firestore ready; webhook/Calendar/Study configured, Gmail disabled.
- Candidate: actual Calendar read passed; 59 exact-source agenda/response tests passed in 2.62s, including both-store replay of the ten scenarios and scope/race/pagination regressions. Existing Starlette deprecation warning only. Synthetic routes used mocked Calendar/Telegram; no live user-message E2E claimed.
- Active production identity, concurrency, timeout, resources, ports, command/args, environment and pinned secret references compared and preserved; only release SHA changed. Auto acceptance retained. Promotion required unchanged baseline traffic.
- Rollback revision: chronos-start-list-e9add15. Sanitized local receipt: .tmp/today-replies-release-a1915b5.json.
- User-visible: compact /today date/time/title layout and new-message reply bindings now active. Existing Telegram messages cannot acquire bindings retroactively; issue /today again before replying.
- Deployment complete. Next: publish sanitized evidence through docs-only PR; no additional application deployment needed. Other computers not verified synchronized.
