# CI natural intent regression coverage

- Updated: 2026-10-21T11:50:39.9900386+08:00 (Asia/Taipei).
- Branch/base: main / b034bee108c4f8698d5bb9b172a7ba12c3574c81. Hook confirms clean/current; reviewed status and welcome handoff. No overlapping active edits.
- Scope: diagnose repeated Chronos CI failures; adapt obsolete integration fixtures to the natural-language entry flow. No production deployment or Forseti changes.
- Evidence: GitHub run 39539989879 fails both Python jobs; Python 3.12 reports 2 failed, 1177 passed. Failover mock returns task schema to intent classifier; closed-task assertion expects removed English copy. Lint, compilation and extension job pass.
- Decision: preserve real classifier/parser and failover/dedup coverage; update provider mock by requested schema. Assert closed tasks remain closed without AI calls.
- Implemented/verified: fixes complete; full local Python suite: 1179 passed (one existing Starlette warning), 117.20s. Docs: 58 links / 36 files, zero missing; git diff --check passed. Prior selected tests missed these regressions.
- Next: no remaining CI repair work. Historical failed runs remain as history; production unchanged.
- Commit/push: a1724db21829f266133d8339af7e5978de70b6a0 pushed to origin/main. GitHub run https://github.com/Yili-code/Chronos/actions/runs/39540664595: Python 3.11, Python 3.12 and extension jobs all completed successfully. This documentation-only follow-up uses [skip ci]; no code changes after the validated commit. No cancelled approach.
