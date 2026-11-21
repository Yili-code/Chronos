# Today-list response audit

- Updated: 2026-10-24 (Asia/Taipei). Base 40e72ec; branch codex/today-response-audit; isolated worktree .worktrees/today-response-audit. Fetched remote; parent local work preserved.
- Authorization: ten representative user replies, sequential test/fix/retest, synthetic records only; no production mutation or deployment.
- Fixed cases: named completion; ordinal reschedule; ambiguous target; missing new time; standalone Calendar event operation; nonsense input; expired list; changed task snapshot; mixed task/event batch; out-of-range ordinal.
- Each case must pass on SQLite and Firestore fake before starting the next. Use live configured interpreter on synthetic inputs where reachable, capture validated outputs for replay through real webhook/router; explicitly distinguish mocks from live transport.
- Initial observation: agenda sends have no persistent reply binding; reply routing falls back to course progress. Case 1 not yet executed.
- Scope initially tests/test_today_response_audit.py, synthetic fixtures/report, main.py/intents.py/ai.py and a small agenda-context module if confirmed necessary. Other active handoffs cover deployed formatting/start; no overlapping edits found in this isolated checkout.
- Implemented/verified: none yet. No alternatives cancelled. Commit/push pending. Next: case 1 only.

- Stage update 2026-10-24 (Asia/Taipei): cases 1–2 passed both stores in order. Case 1 fixed missing agenda reply binding after observed course fallback; 94 related regressions passed. Case 2 fixed model selecting global position instead of displayed row; task scope and bound ordinal added. Protected-time refusal at 17:55 retained; legal 18:05 substep passed. Initial assumption that 17:55 was legal corrected in audit report; no scheduling-policy weakening. 32 prior-case/natural-schedule regressions passed before advancing.
- Live configured Gemini used only on synthetic data; validated samples under tests/fixtures/today_responses/. No task/event creation or production sends. Next: case 3 only. Report docs/audits/today-responses-10.md.

- Stage update 2026-10-24 (Asia/Taipei): cases 3–10 executed sequentially, with fixes before advancing for standalone-event misidentification (5), expired-list completion (7), stale-task completion (8), partial mixed batch completion (9). Cases 4/6 behaved correctly; their initial wording-dependent harness failures were corrected to semantic checks. Case 10 passed its range guard without AI.
- Acceptance review caught overly weak assertions in case 3 (implicit target) and case 2 (success data but stale/no-change reply introduced by snapshot guard). Fixed explicit target selection and booked-edit reply handling; all ten then replayed in order on final code, both stores passed. No scheduling policy weakened. Per-page context follows the exact renderer order; commands in replies route normally. Added races/scope regressions.
- Status: implementation and sequential audit complete; full Python suite running. No commit/push/deployment yet. Next: complete full regressions, extension/docs/diff checks, publish atomic PR, CI, merge. Preserve evidence limits in report.

- Final-tree validation 2026-10-24T18:00:23.909931+08:00: ten cases rechecked in order, both stores each; 1,773 complete Python tests passed in 110.72s; 21 extension tests passed; docs/compilation/diff checks passed. First full run (1,771) also passed; final repeat followed a justified extra single-target/multiple-constraint regression. No assertion weakening, scheduler-policy changes, test removal or model switch.
- Added explicit one-target constraints without treating every conjunction as a batch; scoped candidate lists and per-page reply snapshots. Live sample records are synthetic; preliminary unsafe outputs retained and identified as such in report.
- Ready for atomic commit/PR. CI pending. No deployment authorized/performed; no production writes or Telegram messages.
