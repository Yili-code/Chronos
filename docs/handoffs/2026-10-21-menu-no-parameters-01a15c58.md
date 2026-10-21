# Parameter-free menu entries

- Updated: 2026-10-21 11:30 +08:00 (Asia/Taipei).
- Branch/base: main / 871a645; hook reports current history and clean tree. Reviewed PROJECT_STATUS and latest implementation/deployment handoffs; no overlapping active work found.
- Scope: user correction to deployed menu: only show commands immediately useful without arguments. Remove done from owner menu and shortcut help; retain natural completion and legacy /done syntax. Existing /plan without arguments shows status and remains appropriate.
- Files: chronos/telegram_menu.py, chronos/main.py, menu test, README, docs/usage.md and this handoff.
- Status: implementation and local verification complete; production update pending. Update the production menu and help as a continuation of the authorized production rollout.
- Verification: 61 menu/system tests passed with one existing Starlette deprecation warning; git diff --check passed; 58 documentation links across 33 Markdown files, zero missing. Preflight confirms chronos-00250-fiw receives 100% traffic (release 6203480). Live readback pending. No task or calendar mutation needed.
- Next: test, commit/push, deploy help change and verify six-entry menu.
- Commit/push: pending. No abandoned approach.
