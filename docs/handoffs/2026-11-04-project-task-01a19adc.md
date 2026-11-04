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

- Published atomic source 2e3a0c364246e5908397306cb09898037830040c in PR https://github.com/Yili-code/Chronos/pull/127; required remote CI pending. Real task-add request is already fulfilled separately. New runtime deployment has not been attempted.

- Follow-up counterexamples: negated, hypothetical and question-form task wording requests could still enter review when task/field keywords were present. Explicit wording detection now rejects these forms; six both-store regressions added. Final feature suite41 passed; final full source3,256 passed in189.64s, one existing warning. No further scope additions.

- Final source head447bb3385810e3f1dc6720b5948b7f539976b852 required PR CI41780295714 passed all jobs. PR127 merged with expected-head guard as c53534d721df827d85e26f94dd216e69e72e263a and fetched. Exact merge-source CI pending. Owner task-add request fulfilled; no runtime deployment attempted.

- Updated 2026-11-04T13:43:23.769138+08:00: exact merge CI41780638172 passed Python3.11,Python3.12,extension for c53534d721df827d85e26f94dd216e69e72e263a. Code/local tests/remote CI/merge complete. Requested actual task addition fulfilled and verified separately. Runtime routing fix not deployed.
- Prepared root .tmp/project-task-release.py: exact successful merge CI gate, clean archive, zero-traffic candidate, normalized runtime-spec preservation (apart from image/release SHA), unchanged credential references, readiness/status/Calendar reads, exact-source route regressions and baseline-guarded promotion. Helper has not executed. Deployment requires current explicit owner authorization; prior approvals covered earlier releases.

- Owner explicitly approved deployment of this routing fix with existing Calendar/Gmail credentials and settings preserved, zero-traffic verification and 100% promotion. Starting guarded release of exact CI-successful source c53534d721df827d85e26f94dd216e69e72e263a.

- Initial candidate deployment succeeded but first public readiness GET ended with RemoteDisconnected; helper stopped before promotion. Resuming verification of the existing exact-source candidate with bounded GET transport retries only. No HTTP readiness/status/assertion gates weakened. Active baseline must still be chronos-executive-016fdec; compare runtime against that immutable active revision, not the service template already updated to the zero-traffic candidate.

- COMPLETED 2026-11-04T13:48:49.575928+08:00: exact source c53534d721df827d85e26f94dd216e69e72e263a deployed as chronos-project-task-c53534d and verified at100% traffic. Final public readiness ready/Firestore/exact source; authenticated candidate status matches source with Telegram/Calendar/Study/Gmail configured. Actual Calendar read passed before promotion.
- Candidate exact-source regressions220 passed in13.80s. Normalized full runtime spec (apart from image/release SHA) matches immutable active baseline; credential references and settings preserved. Rollback chronos-executive-016fdec retained. Sanitized local evidence .tmp/project-task-release-c53534d.json.
- Owner-requested real task creation/readback is complete separately from tests. Runtime route regressions use mocked model/transport behavior, retaining the wrong model intent; no real-provider or owner Telegram routing E2E claimed. No unrelated task edits or Telegram sends from the manual task addition.
- Code/tests/required CI/merge/approved deployment and requested task addition are complete. Publishing final sanitized handoff through documentation-only PR; no application redeployment needed. Other computers not verified synchronized.
