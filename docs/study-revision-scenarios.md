# Study revision: ten sequential owner scenarios

Date: 2026-10-24 (Asia/Taipei). Baseline: a5d263819bc9b7352473e8b6982d1ac27bf03152.

## Method

Acceptance criteria were fixed before execution. Each scenario ran individually with stop-on-failure, on real SQLite and the strict Firestore transaction fake. A second opt-in run used the existing local model configuration with synthetic tasks only. The real application intent/preparation/receipt and review callback paths ran against isolated storage and a mock Calendar transport. No real tasks, Telegram messages or Calendar events were created or changed by these tests.

A scenario had to pass before the next started. After each product fix, already completed scenarios and the related existing suites were rechecked. Live responses are finite samples, not a guarantee for all phrasings or future provider behavior. Raw synthetic traces remain local; they contain no production records or credentials.

## Results

| # | Simulated owner input or condition | Acceptance | Final result |
|---|---|---|---|
| 1 | Tomorrow, ISPM Ch1/Ch2 at least 120 minutes; other subjects unchanged | Review before writes; retain a longer 150-minute chapter and the other subject's exact original event | Passed after fix: explicit keep-unchanged wording now makes other sessions fixed |
| 2 | Original English correction with no repeated date; Ch1/Ch2 unfinished | Use delivered tomorrow context, include all affected chapters, retain old history and open status | Passed; unfinished sessions queued only after Allow |
| 3 | Those two chapters need a bit more time; replan | Ask which chapters/duration rather than guess or ask for a window to free | Passed; no review or mutations |
| 4 | Chapter minimum is explicit, date absent and no recent agenda | Ask date even if a provider invents a plausible day | Passed after fix: independent date-evidence guard, including expiry of editing context |
| 5 | ISPM Chapter 8 is unfinished, but no Chapter 8 task exists | Identify missing target and ask; do not substitute another chapter or create a task | Passed; model questions and unchanged data verified. Wording-coupled test assertions were corrected without relaxing behavior |
| 6 | Spent 30 minutes, still need at least 120 minutes tomorrow; other subject fixed | Do not subtract spent time from explicit remaining work or mark completed | Passed; at least 120 minutes queued, history/open status preserved |
| 7 | Quoted clear_tasks JSON explicitly marked do-not-execute, followed by real revision request | Treat pasted content as data; only preview intended study revision | Passed; no task deletion or Calendar writes |
| 8 | Five chapters x 120, database 90, calculus 180; 600-minute day | Report infeasibility and ask tradeoff; preserve every original event/task/state | Passed; 870-minute demand was not partially applied, shortened or silently omitted |
| 9 | At least 181 minutes per chapter; provider clamps to 180 | Explain supported limit and ask adjustment, without changing the plan | Passed after fix: explicit 10–180 minute check before parsing |
| 10 | External conflict after preview; old Allow; fresh preview and repeated Allow | Reject stale approval before writes; fresh review avoids conflict; valid Allow applies once | Passed; exactly one final move, external event preserved |

Each case passed both deterministic backend runs and both opt-in live-model backend runs. Cases 1, 4 and 9 required product changes. Case 5 exposed assertions tied too narrowly to English synonyms; recorded replies already correctly identified the missing chapter. The test now validates the structured non-actionable question, referenced chapter and unchanged data rather than an exact sentence.

## Changes and reproducibility

- [Study revision implementation](../chronos/study_revision.py): keep unchanged sessions fixed; require original/date-context evidence; explain unsupported explicit minimums before model parsing.
- [Scenario tests](../tests/test_study_revision_scenarios.py): ten named scenarios, parameterized across both storage contracts. Run normally for deterministic regression; opt in to live models with CHRONOS_SCENARIO_LIVE=1 and CHRONOS_SCENARIO_ENV_FILE pointing to an existing private settings file. Never commit that file.
- [Existing revision tests](../tests/test_study_revision.py): synthetic instructions now include an explicit date instead of relying on the mock provider to invent one.

Related existing revision/replan/intent suites passed 203 tests after the final product fix. Full final-tree Python, extension, CI and release evidence are recorded in the [handoff](handoffs/2026-10-24-study-scenarios-01a16e12.md).

## Limits

These tests do not establish a real Telegram approval-to-Google-Calendar-write end-to-end result, live Firestore behavior, all possible paraphrases, or equivalence between local and production model settings. The reported 30 minutes in case 6 were not automatically posted to the feedback ledger. Follow-up answers after every clarification, cross-midnight context, automatic multi-day splitting and exhaustive scheduling optimality were outside these ten acceptance cases. Production deployment/readiness evidence is separate from this isolated scenario evidence.
