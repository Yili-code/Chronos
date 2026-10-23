# Deploy acknowledged task changes

- Updated: 2026-10-23T14:57:57+08:00.
- Authorized: deploy to production, per user request in this chat.
- Target: ee606d92bdc1b5dfd0f007f7c6f1a86e24bdf82c (PR #3 merge; includes PR #1/#2). Later main features excluded. Base branch codex/acknowledge-no-change; existing local notes preserved.
- Preflight: exact merge SHA push CI 39911148973 succeeded; Python 3.11, Python 3.12, extension jobs verified. Production yili-chronos-prod / asia-east1 / chronos has 100% to chronos-00254-geg (e10a74f).
- Decision: deploy clean Git archive at target SHA without traffic, updating only release environment/commit label. Preserve live runtime configuration, secrets, scheduler and historical tags. Candidate readiness/release identity gate before promotion. Rollback chronos-00254-geg.
- Status: candidate deployment pending. No domain data repair or synthetic Telegram message authorized/needed. Verification will use health/readiness and authenticated status.
- Next: candidate, verify, promote and read back traffic/version.

- Candidate chronos-00256-pac is ready at 0% traffic. Live synthetic Gemini rejected batch schema with HTTP 400; existing single-task schema succeeded. Expanding references alone did not resolve it. Removing only provider minItems/maxItems succeeded, retaining original references and all local Pydantic constraints; five chapters each tagged database and acknowledge intent verified. No data/Telegram mutation.
- Release fix: codex/batch-schema-release starts from ee606d9, excluding later unrelated main work. Only omit provider array bounds; local TaskBatchOutput still rejects empty/>20 batches. Full tests and PR/CI required before deploying corrected commit. Old production remains at 100%.

- Fix verification: complete Python suite 1,230 passed in 43.82s; documentation/diff checks passed. Provider-facing schema regression added and >20-item response rejection retained. Deployment scope question pending because newer main features exist; work on compatible fix/PR can proceed independently.
