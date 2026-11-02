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
