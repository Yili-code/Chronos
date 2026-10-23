# Course hashtags

- Updated: 2026-10-23 Asia/Taipei.
- Base: 7dc2264; branch codex/course-hashtags; isolated .worktrees/course-hashtags.
- Scope: deterministic course ID to English abbreviation mapping, assignment creation in both backends, legacy display, regression coverage.
- Diagnosis: assignment creation stores raw course ID as project; card displays it verbatim.
- Decision: retain source course identity, store readable course tags for new assignments, resolve known legacy IDs at rendering without rewriting owner data. Unknown IDs are not guessed.
- Verification: pending. Commit/push/CI pending. Deployment not authorized by this task.

- Verified: full Python suite 1,375 passed in 49.73s (one existing Starlette warning); SQLite and strict Firestore fake cover all seven courses and unknown course, retained assignment identity; legacy HTML/text/card rendering and owner aliases covered. Docs 60 local links, 46 Markdown files, zero missing; diff check clean.
- Initial sandbox/temp fixture errors resolved with absolute basetemp and approved test execution. No application failure remained.
- Next: atomic commit, PR and exact-head CI verification. No production data writes or deployment.

- Integration: latest origin/main 79e8086 included concurrent course habits and event cleanup. Resolved append-only usage documentation conflict by preserving both additions. Integrated full suite: 1,524 passed in 56.10s; docs 85 links in 61 files, zero missing. Existing warning unchanged.
- Published: feature commit 5a729a3; PR https://github.com/Yili-code/Chronos/pull/31. Integration commit and final CI pending. No production deployment.
