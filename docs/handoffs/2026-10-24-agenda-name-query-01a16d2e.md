# Named calendar query correction

- Updated: 2026-10-24 (Asia/Taipei). Base 5c0d002; branch codex/agenda-name-query; isolated worktree .worktrees/agenda-name-query. Remote fetched; parent local work preserved.
- User evidence: asks whether there is an AtCoder competition after nine; response lists Codeforces with the old duration/title layout, without answering the named-event condition.
- Scope: reproduce exact natural-language query using configured live Gemini on synthetic dates/events, retain time and name conditions, ensure unrelated overlapping events cannot answer a named query; use compact event rows for query replies if verified appropriate.
- No claim about actual personal AtCoder schedule. No production task/event mutation or deployment authorized for this correction.
- Status: investigating actual route/schema; no event-name filter exists in Intent or agenda handler. Test reproduction pending; no cancellation alternatives, commit/push pending. Next: reproduce before editing behavior.

- Verified 2026-10-24T18:32:22.682295+08:00: live configured Gemini reproduced correct 21:00–midnight time bounds on synthetic clock, while baseline route returned Codeforces for an AtCoder query. Intent had no name filter.
- Implemented agenda_keywords schema/prompt contract, literal normalized title filtering (case/full-width/spacing insensitive; all provided terms required), rejects invented/non-word terms before Calendar read, explicit named empty result with escaped terms and query range. Calendar failures remain failures. Read-only window responses now use compact rows; today/tomorrow/evening behavior retained.
- Live re-interpretation of original wording captured agenda_keywords [AtCoder] and correct time bounds in tests/fixtures/named_agenda_query.json; positive/negative replays pass both database stores. 139 existing/focused regressions passed; 13 focused edge cases passed. No real task/event writes or Telegram send. Complete Python suite running.
- Next: finish full tests, extension/docs/diff checks, atomic PR and CI. No deployment for this correction yet. Actual personal AtCoder schedule not read or inferred.

- Final-tree validation 2026-10-24T18:34:12.633615+08:00: 1,786 Python tests passed in 114.05s (existing Starlette deprecation warning); 21 extension tests passed; 87 documentation links/97 Markdown files, compilation and diff checks passed. Ready for atomic PR; CI pending. No deployment performed.
