# Ordered rescheduling priority and secretary replies

- Updated: 2026-10-25 (Asia/Taipei).
- Base: 2f0b1c8d1db4f5c113358b12a5fe8e9eda36f230; branch codex/sequence-reply; isolated .worktrees/sequence-reply.
- Scope: fix unnecessary group postponement and apply existing secretary voice to ordered review/clarification messages. Existing root and release worktrees preserved.
- Cause: preview retains feasible late predecessor originals before considering earlier free group slots. Existing reply-style policy was not applied to newly introduced deterministic sequence templates; memory alone cannot govern those strings.
- Plan: reproduce early-slot reuse with both database backends, prioritize earliest feasible dependency placement, retain durations/identity/completion/conflict/approval boundaries, compact human-readable dates and consolidated questions without normal internal IDs. Add output-contract tests and update durable style rules.
- Validation/publication/deployment pending; no real Calendar mutations. Existing review should not be approved; regenerate after release.
- Reproduced before change: both SQLite/Firestore cases delayed the first 120-minute session to its late original date instead of reusing a selected later chapter slot. Both failed the intended earliest-slot assertion.
- Implemented: earliest feasible forward placement of the entire selected group, with all selected old slots released; original durations/IDs, completion evidence, constraints and explicit Allow preserved. A date-only ambiguity reply identifies an event without pinning its original date.
- Presentation: concise secretary introduction, readable local date/time candidate rows, references only for identical times, compact From/To reviews and shared uncertainty once. Same displayed local time is accepted in replies; reference-only replies route to the active clarification. Existing style policy and output-contract requirements strengthened in AGENTS.md/docs/reply-style.md.
- Verified: feature suite 53 passed after priority correction; initial full suite 1,900 passed in 127.46s. Final full rerun after timezone/reference selection checks is in progress. Extension 21 passed; docs 91 local links / 102 Markdown files, zero missing; compile/diff checks passed.
- Actual read-only check: current Calendar plus saved review constraints produce an earlier first session and a complete review. Production Firestore mutations intercepted in an in-memory shadow; no Calendar patch, Telegram send or production state change. Private details remain untracked in .tmp. This is not real approval-to-write E2E.
- Next: final suite, PR/required CI, merge-source CI, zero-traffic deployment verification and guarded promotion under existing deployment authorization.
- Final-tree validation complete: 1,900 Python tests passed in 126.76 seconds; existing Starlette deprecation warning only. Final feature suite 59 passed in 2.90 seconds, including local-time and identical-time reference replies. Extension 21 passed; docs/compile/diff passed. No live Calendar write or Telegram rendering verification claimed.
- Ready for atomic publication; remote CI and deployment pending.
