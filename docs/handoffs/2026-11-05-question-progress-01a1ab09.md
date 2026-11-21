# Question-count study feedback

- Updated: 2026-11-05 Asia/Taipei. Base ae3b0ad; branch codex/question-progress; isolated worktree .tmp/question-progress.
- Scope: question-count feedback, contextual fractions, incremental replies and unit-correct continuation. Preserve legacy page reports and transactional receipt/session validation.
- Sync: manual hook failed PermissionError; explicit git fetch succeeded. Branch created from fetched origin/main. Root unrelated local documentation preserved.
- Diagnosis: fractions require page suffix; partial reply and continuation assume page measurements. No production actions authorized or performed.
- Implementation/validation/commit/PR pending. Files: study_feedback.py, learning_calendar.py and relevant tests.

- Implemented: deterministic question and generic-count fractions, explicit question totals/counts, task/draft-aware units, token-bound split replies, unit-correct acknowledgement and remaining-work estimate/title. Page and legacy pipe formats retained; switching explicit units clears incompatible draft measurements while retaining reported duration.
- Validation: initial feedback suites 351 passed; subsequent new-count/webhook subset 26 passed, including both stores, restart, owner time, duplicate webhook receipt and no Calendar writes. Final full Python suite running; Ruff undefined-name checks, compilation, diff and docs (129 links, zero missing) passed.
- No live Telegram/Calendar writes or deployment. Next: final full-suite result, atomic commit/push, PR and required CI before merge. Deployment requires explicit authorization under AGENTS.md.

- Final local validation: 4,608 Python tests passed in 211.24s, one existing Starlette deprecation warning. Final source includes webhook split-reply/replay and legacy pipe compatibility; no failures skipped or weakened. Ready for atomic publication and remote CI. Deployment remains unauthorized.
