# CI natural intent regression coverage

- Updated: 2026-10-21T11:49:11.4579197+08:00 (Asia/Taipei).
- Branch/base: main / b034bee108c4f8698d5bb9b172a7ba12c3574c81. Hook confirms clean/current; reviewed status and welcome handoff. No overlapping active edits.
- Scope: diagnose repeated Chronos CI failures; adapt obsolete integration fixtures to the natural-language entry flow. No production deployment or Forseti changes.
- Evidence: GitHub run 39539989879 fails both Python jobs; Python 3.12 reports 2 failed, 1177 passed. Failover mock returns task schema to intent classifier; closed-task assertion expects removed English copy. Lint, compilation and extension job pass.
- Decision: preserve real classifier/parser and failover/dedup coverage; update provider mock by requested schema. Assert closed tasks remain closed without AI calls.
- Implemented/verified: fixes complete; full local Python suite: 1179 passed (one existing Starlette warning), 117.20s. Docs: 58 links / 36 files, zero missing; git diff --check passed. Prior selected tests missed these regressions.
- Next: run full suite, commit/push scoped fixes, verify remote CI.
- Commit/push: prepared for scoped commit/push; remote CI verification pending. No cancelled approach.

