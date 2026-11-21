# Reset clarification reliability

- Updated: 2026-11-02 (Asia/Taipei).
- Base: b1290a4; branch codex/reset-confirmation-01a19adc; isolated worktree.
- Scope: screenshot clarification loop; deterministic historical choice, retained parsed request and dates, type isolation, expiry/cancel/replay/no-write/result regression coverage.
- Root checkout unrelated modifications preserved. Fetch succeeded before isolated worktree creation.
- Deployment authorized by current user request; PR and exact-source CI required.
- Implemented/verified: diagnosis only. Existing tests change the AI response on follow-up and miss the loop.
- Pending: implementation, complete tests, PR CI, merge-source CI and guarded deployment.
- Commit/push: none.

## Ten acceptance checks

The screenshot proves a repeated historical-scope question after an explicit answer. These ten checks break that workflow into observable requirements; they are not ten independently proven screenshot defects.

1. Explicit inclusion proceeds even if the AI output remains null.
2. Future-only answers leave historical sessions untouched.
3. Whitespace, punctuation and English short answers use one deterministic parser.
4. The original instruction survives the follow-up.
5. The parsed destination start/end survive the follow-up.
6. Activity category and unrelated Calendar events remain unchanged.
7. A clarification older than two hours requires a fresh complete request.
8. Cancellation, including punctuation, performs no Calendar writes.
9. Duplicate updates return the prior result without duplicate writes; reordered standalone candidates require a fresh request.
10. Success is returned only after existing execution reconciliation; fixed historical dependency anchors are included in readback rather than spuriously reported as missing.

- Implementation: store validated pending output only for the historical-scope question; apply the explicit answer directly, with no second AI call. Refresh live tasks, events, constraints and execution guards. Legacy journals without pending output retain model parsing, with explicit include/future choice taking priority.
- Additional regression exposed a fixed historical anchor omitted from execution agenda reads; agenda now includes the earliest fixed anchor before checking it.
- Feature suite: 209 passed before expiry terminal-state refinement; complete suite running on final behavior. Extension: 21 passed. Docs: 98 links, zero missing. Diff check passed.
- Environment: initial test attempt lacked basetemp parent; corrected. Restricted Windows asyncio run stalled; its two dedicated scope2 processes were stopped and tests rerun with local communication access.
- No production Calendar/task/Telegram writes have been used for validation. PR/merge/deployment pending.

- Full run: 2,989 passed, one existing course-habit webhook test failed in 166.07s. Reproduced that failure in its standalone module: 10 passed, one failed. The fixture creates its proposal on 2026-10-22, while webhook main uses actual current time, correctly rejecting the seven-day-expired proposal on 2026-11-02.
- Corrected only that test to give webhook main the fixture clock, preserving both acceptance/replay assertions and production expiry protection. This validation prerequisite is a separate atomic commit. Full final-tree suite being rerun.

- Final local validation: all 2,990 Python tests passed in 165.72s (existing Starlette deprecation warning only); 21 extension tests passed; source compilation, documentation links and diff checks passed. Final source changed only in indentation after the first full run; second full run includes the terminal expiry behavior and fixed clock.
- Prerequisite commit: 26daffd. Preparing separate atomic clarification commit and PR; no push/merge/deployment yet.

- Published clarification commit 06b443ea0ae70e5c06e37075797d0e34e3fc8d43 plus prerequisite 26daffd in PR https://github.com/Yili-code/Chronos/pull/120. PR CI running; merge/deployment pending.

- PR CI 41398989379 passed Python 3.11, Python 3.12 and extension. PR #120 merged with expected-head guard as f490f869679c86c3745108fcd6e7306582cbc153. Exact merge-source CI pending; deployment has not started.
- Live read-only baseline: chronos-command100-b1290a4 receives 100% traffic; /ready is ready/Firestore and reports b1290a4af4051c3792e2130ab597321d5a210c61. No real owner Calendar writes.

- Exact merge-source CI 41399382664 passed all required jobs for f490f869679c86c3745108fcd6e7306582cbc153. Guarded zero-traffic deployment starting from an exact Git archive. Baseline/rollback chronos-command100-b1290a4 retained. No promotion success claimed yet.

- Updated 2026-11-02T14:31:05.731418+08:00: candidate chronos-reset-confirmation-f490f86 deployed at zero traffic. Public readiness/exact SHA, authenticated status, runtime identity/resources and environment/secret-reference comparison passed. Calendar-read gate returned HTTP 503; guarded helper stopped before traffic promotion.
- Read-only comparison confirms BOTH baseline and candidate reject Calendar authorization as expired/revoked. Local Calendar grant also fails. This is an existing dependency failure, not evidence of a code regression. Production remains chronos-command100-b1290a4 at 100%; do not mark deployment complete.
- Exact archived merge source independently passed 249 study-reset/event-reset/reset-conversation regressions in 11.20s. No production event/task/message mutations during validation.
- Opened existing PKCE loopback Calendar authorization helper in the default browser; only the user can complete Google consent. Running exec session 2318 waits up to 15 minutes and saves credentials only to ignored root .env. User input requested; no response or completed authorization yet. Consent URL/code/token must never be printed or committed.
- Next: confirm helper success; verify local grant/account. Publish only the refreshed Calendar token as a pinned Secret Manager version while preserving runtime settings and zero traffic; compare expected secret/reference change, verify candidate Calendar reads, baseline guard and exact SHA, then promote and re-read final traffic. Existing calendar_deploy helper updates additional settings and traffic, so do not invoke it blindly.
- Local deployment source/archive/helper are under root .tmp/reset-confirmation-*; release helper stopped at Calendar gate. Do not rerun it unchanged: source directory already exists. Code/CI/merge complete; production promotion pending user reauthorization.

- Shared final evidence and the authorization blocker in merged PR #120 description. Latest detailed handoff is local in both task worktree and parent checkout; no claim of other-machine synchronization. Awaiting Google consent; production promotion remains incomplete.

- Updated 2026-11-02T16:09:32.598158+08:00: original OAuth helper timed out with nothing saved. User reported no page/localhost unavailable. Restarted existing helper (exec session 12507); Google callback succeeded, account/grant validation passed, credentials saved only in ignored root .env. Helper exited successfully. No authorization code/token printed or committed.
- Prepared release plan: verify new local grant and matching existing cloud client/account, upload only the refreshed Calendar token to the existing chronos-calendar-refresh-token Secret Manager secret, pin the new version in a zero-traffic revision using the already CI-verified candidate image, compare only release SHA/token-reference differences, verify Calendar read, then guarded 100% promotion and final readback.
- Automatic approval review rejected the combined credential-publication/promotion command BEFORE execution: deployment authorization did not explicitly authorize sensitive refresh-token transfer to production Secret Manager. No cloud token upload, authorization-resume script creation or promotion occurred from the rejected command. Must not retry or indirectly bypass; obtain explicit user approval for this precise transfer and subsequent guarded promotion.
- Code/CI/merge and 249 candidate-source regressions remain complete. Latest actual traffic verification is original baseline at 100%; production promotion remains incomplete. Awaiting explicit approval for credential publication, not another Google consent.

- Updated 2026-11-02T16:10:48.902896+08:00: user explicitly approved publishing the refreshed Calendar token to the existing production Secret Manager secret and completing guarded promotion. Resumed approved publication with client/account identity checks, immutable verified image reuse, zero-traffic deployment and Calendar read gates. Exec session 68194 is active; no success claimed until final readback.

- COMPLETED 2026-11-02T16:12:04.206884+08:00: authorized credential publication and guarded promotion succeeded. Exact application source f490f869679c86c3745108fcd6e7306582cbc153, revision chronos-reset-confirmation-f490f86-auth receives 100% traffic. Final public readiness is ready/Firestore and reports exact SHA; final authenticated Calendar read is verified. Webhook, learning calendar and study configured; Gmail disabled.
- Candidate reused the immutable CI-verified application image. New OAuth grant was locally validated and client/account matched existing production configuration before storing only the refresh token in the existing secret under explicit user approval. Baseline-to-candidate configuration comparison allowed only application release SHA and refreshed Calendar token reference; runtime identity/resources/concurrency/timeout preserved.
- Rollback revision chronos-command100-b1290a4 retained. Prior baseline grant is expired; reverting application traffic alone would also restore that expired pinned credential. If application rollback is needed, retain the refreshed token reference in the rollback candidate and verify Calendar reads before promotion.
- All requested implementation, testing, CI, merge and production deployment work is complete. No synthetic production task/Calendar/message writes were performed. Real owner Telegram request-to-Calendar-write E2E and Telegram visual rendering remain unverified; no claim otherwise.
- Sanitized deployment evidence: root .tmp/reset-confirmation-release-f490f86.json. Preparing final documentation-only evidence publication; no application redeployment required for that documentation change. Other machines not verified synchronized.
