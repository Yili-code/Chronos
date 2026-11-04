# Named work versus task wording review

- Updated: 2026-11-04 Asia/Taipei. Base 47cc142; branch codex/project-task-01a19adc; isolated worktree.
- Scope: a standalone improvement work item must not trigger global existing-task wording suggestions; screenshot subject Mnemosyne, unrelated InnoServe review. Preserve explicit wording/tag-review requests and clarify ambiguous/nonoperative input.
- Root unrelated modifications retained. Read latest executive handoff; its source/runtime validation is historical, not proof of current new behavior.
- Diagnosis: model task_review intent dispatches to /reviewtasks for the whole list without checking whether owner requested wording/tag changes. Fix prompt semantics plus a deterministic dispatch boundary, rather than a project-name special case.
- Pending implementation, regressions, full suite, PR CI and merge. Current request explicitly asks for task addition; new application deployment requires clear current authorization before traffic changes.
- Commit/push/deployment: none. No production writes yet.

- Implemented explicit task-wording/tag request detection, a narrow concrete-work guard for misclassified task_review, safe clarification for questions/references/bound context, and classifier prompt examples distinguishing project work from task presentation. No hard-coded project-name routing.
- Feature suites: 93 review/new-work regressions passed, then final new-work suite 35 passed with actual webhook+replay coverage. Both stores, original input preservation against hallucinated instructions/IDs, unrelated-task invariance and explicit review behavior covered. Extension21, docs110 links/0 missing, compilation/diff passed. Full suite running.
- User explicitly requested the real task addition; fulfilled via existing production TaskService/Firestore transactional operations, with duplicate lookup and a per-request idempotency record. Exact requested work exists open with no deadline; readback verified. No unrelated task changes or Telegram sends. This is a real requested write, not synthetic validation. Private runtime IDs/records are excluded from the shared handoff.
- Initial post-write terminal output failed because Windows stdout lacked Chinese encoding; the transaction and readback had completed. Idempotent rerun used ASCII-safe JSON and verified the same task without creating another.

- Final local full suite: 3,250 passed in 175.10s, existing Starlette warning only. Local compilation, documentation/diff and extension21 checks passed. Preparing atomic source PR; real requested task addition complete, runtime routing fix not deployed.
