# Contextual Telegram conversation replies

- Updated: 2026-10-24 (Asia/Taipei).
- Base: 92b8b62; branch codex/conversation-natural; isolated checkout .worktrees/conversation-natural.
- Scope: distinguish greetings, gratitude, identity questions and explicit no-change replies without provider calls; preserve mixed requests, existing formal English policy, receipts and approval gates.
- Sync: original branch hook reports current tracking history; isolated checkout uses fetched origin/main. Unrelated local work preserved.
- Finding: every acknowledgement currently uses the same reply; greetings and identity questions unnecessarily enter task classification.
- Validation: pending full Python and extension suites, docs and diff checks. No live Telegram or Calendar writes; no deployment authorization.
- Commit/push: none. Next: implement conservative whole-message recognition and regression tests, validate and publish PR.

- Verified: complete Python suite 1,652 passed in 106.03 seconds; existing Starlette deprecation warning only. Both SQLite and strict Firestore fake exercise new social-turn routing, mixed requests, pending-context preservation and duplicate receipt replay. Three webhook social-turn cases verify one delivery for repeated updates.
- Extension: 21 tests passed. Documentation: 86 local links / 80 Markdown files, zero missing. Compilation and git diff --check passed.
- Environment: default Python lacked APScheduler; switched to existing project virtual environment. Initial sandbox test attempts stalled in Windows asyncio socketpair creation before product logic; diagnosed by traceback, stopped only those test processes and ran the complete suite with approved local socket access. No assertions weakened.
- Source cleanup after test import: removed an unused import only; compile/diff checks repeated successfully.
- Implemented: complete-message social reply recognition in reply_policy.py, shared prepare_message integration, regression coverage and usage documentation. No changes to user content, model, approval gates, Calendar behavior or delivery transport.
- Limit: automated/mocked integration evidence does not establish live provider accuracy, delivery or human-rated conversational naturalness. No live service writes or deployment performed. Preparing atomic commit and PR; remote CI pending.
