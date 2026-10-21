# Deploy agenda, then simplify welcome layout

- Updated: 2026-10-21T11:41:44+08:00 (Asia/Taipei).
- Branch/base: main / e10a74f; hook reports clean/current. Reviewed PROJECT_STATUS, prior agenda and production handoffs and current welcome renderer; no overlapping active work identified.
- Scope/order: user explicitly authorized deploying the pending agenda formatting first, then optimizing the attached /start layout. Remove duplicated menu command instructions and category buttons; use short separately spaced examples.
- Preflight: production routes 100% to chronos-00252-ceg. Deploy e10a74f from a clean Git archive without traffic, verify release identity, then promote. Preserve runtime settings and existing tags.
- Status: agenda deployment completed first. Release e10a74f68fe65601a7e009964535c6cf347dce88 is live as chronos-00254-geg at 100% traffic. Candidate readiness matched before promotion; public /ready and /live passed. Then simplified HELP_TEXT to title and three line-separated examples, removed menu command descriptions and category buttons. Empty markup clears buttons on old help-card return; legacy category callbacks remain compatible. Welcome source change is complete but not deployed.
- Verification: prior agenda source passed 66 tests and exact output checks. Welcome/system tests: 60 passed, one existing Starlette deprecation warning. git diff --check passed. Documentation check: 58 links in 35 Markdown files, zero missing. No live task/calendar mutation or synthetic Telegram messages.
- Next: welcome layout may be released with subsequent deployment authorization. Current production remains e10a74f; rollback reference chronos-00252-ceg. This request sequenced deployment of the existing change before the new layout edit.
- Commit/push: reviewed welcome code, existing test adjustments and this handoff prepared for commit/push to origin/main; verify tracking readback. No abandoned approach.
