# Sequential natural-language rescheduling audit

- Updated: 2026-10-24 (Asia/Taipei).
- Base: bd97fdd; branch codex/natural-schedule-audit; isolated checkout .worktrees/natural-schedule-audit.
- Authorization: ten realistic natural-language schedule edits, evaluate each, fix failures before advancing. Synthetic tasks/calendars only; no real task/event mutations or deployment authorization.
- Evidence model: sequential live provider interpretation on synthetic data, replay returned schema through real routing and booked-edit behavior with mocked Calendar/Telegram; distinguish understanding from side effects. Clarification/conflict refusal is correct only where required by the case.
- Initial findings: booked reschedule accepts exact/keep only; window/soon rejected, and exact times are forced through a 15-minute grid. Shared schedule-edit prompt only describes unapproved proposals.
- Validation pending; no commit/push/deployment yet. Next: execute case 1, stop/fix/retest on each failure, then full suite and PR CI.

- Sequential case results: 1–3 passed both stores; case 4 first failed plan_request routing, then omitted window end; classifier/prompt and bounded booked-window search fixed and passed before case 5. Case 5 first refused today-only earliest availability, then omitted window end; explicit today boundary and required provider window_end fixed and passed before case 6. Case 6 passed. Case 7 parsed correctly but quarter-hour validation blocked 14:10; exact-time opt-out added, both-store test passed before case 8. Cases 8–10 passed with clarification/conflict refusal as appropriate.
- Evidence: live configured Gemini interpreter on synthetic tasks, captured schema outputs in tests/fixtures/natural_schedule_edits.json, real webhook/router/reschedule replay using mocked transports and both backends. No live Calendar, Telegram or real task mutation. Initial case-1 console encoding error occurred after the valid sample was saved; reused it rather than making another request.
- Runtime safety: requested window must be timezone-aware, positive and at most seven days. Availability stays bounded; only the verified owned source event is excluded. Exact owner edits can bypass alignment, not deadline/capacity/class/conflict/lead-time checks. Existing ETag/marker retry reconciliation preserved.
- Tests: each case passed before advancing; malformed/missing/reversed/oversized windows and fully occupied bounded windows added as non-mutation regressions. Complete Python run pending. Extension 21 passed; docs, source compilation and whitespace checks passed.
- Report: docs/audits/natural-schedule-edits-10.md includes all ten inputs, first-attempt results and interpretation/behavior limits. Individual first-attempt results are sequential, not a claim that all cases were run on one unchanged baseline.

- Final-tree validation: complete Python suite 1,733 passed in 109.74s; one existing Starlette deprecation warning. Ten live-output replay cases run against both stores, plus ten invalid/occupied-window checks; all original tests retained. Extension 21 passed. No assertion weakening or model switch.
- Documentation-only additions after the full run: audit clock/usage/handoff text; recheck docs and diff before publication. Preparing atomic PR; remote CI pending. No deployment authorized or performed.
