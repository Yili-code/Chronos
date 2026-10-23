# Course habit intent audit

Date: 2026-10-23 (Asia/Taipei). The ten synthetic replies are defined in `scripts/audit_course_habits.py`. They exercise the actual course-reply preparation and local SQLite persistence, using the production-configured Gemini model. No production preference, task, Calendar or Telegram writes are performed by the audit.

The previous numbered-only parser matched the intended action in 2/10 samples. These are targeted examples, not a representative accuracy benchmark. The revised audit also checks that no task is created, non-habit replies preserve preferences, partial updates retain the unchanged item, references use the shown card, and review wording/minimum counts survive extraction.

| Case | Reply type | Expected behavior |
| --- | --- | --- |
| 1 | Explicit `1.` / `2.` recurring reply | Save two course habits |
| 2 | Unnumbered recurring prose | Extract and save two habits |
| 3 | Chinese numerals | Extract and save two habits |
| 4 | Bullet list | Extract and save two habits |
| 5 | English recurring request | Save two habits preserving English and minimum count |
| 6 | Change only the second item | Retain the first item |
| 7 | Reuse the displayed two-item proposal | Save the shown routine without accepting tasks |
| 8 | Stop recurring suggestions | Disable future course habit proposals |
| 9 | One-time report, do not remember | Record progress without changing habits |
| 10 | Reference to an unspecified routine | Clarify without changing progress or habits |

See [old-parser results](course-habit-baseline.json) and [live sample results](course-habit-live.json). The final targeted sample passed 10/10 checks using `gemini-3.5-flash-lite` (nine model calls and one deterministic clarification). A first revised live pass handled 9/10 cases; the missing-reference case returned an invalid structured response and failed closed. A deterministic clarification guard now handles that case before any model call. Further review found a substitution of the saved “study” default for an explicit “review” request; the prompt and output checks were tightened before the final sample run.

Mock tests separately verify bounded schemas, transport compatibility, both database backends, rollback and replay, failure behavior, preference races, missing-reference clarification and the actual webhook route. Provider samples establish observed behavior for these inputs only; they do not prove general language accuracy or real Telegram delivery.
