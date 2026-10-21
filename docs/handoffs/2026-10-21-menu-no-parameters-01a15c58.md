# Parameter-free menu entries

- Updated: 2026-10-21T11:33:39+08:00 (Asia/Taipei).
- Branch/base: main / 871a645; hook reports current history and clean tree. Reviewed PROJECT_STATUS and latest implementation/deployment handoffs; no overlapping active work found.
- Scope: user correction to deployed menu: only show commands immediately useful without arguments. Remove done from owner menu and shortcut help; retain natural completion and legacy /done syntax. Existing /plan without arguments shows status and remains appropriate.
- Files: chronos/telegram_menu.py, chronos/main.py, menu test, README, docs/usage.md and this handoff.
- Status: complete. Released 1c9cd43aa8ab15c7ae10e92fdeec67156fc247fe as chronos-00252-ceg; candidate readiness matched before promotion. Traffic readback confirms 100%; production /ready and /live passed. Update the production menu and help as a continuation of the authorized production rollout.
- Verification: 61 menu/system tests passed with one existing Starlette deprecation warning; git diff --check passed; 58 documentation links across 33 Markdown files, zero missing. Preflight confirms chronos-00250-fiw receives 100% traffic (release 6203480). Live owner menu exact readback confirms task, plan, today, tomorrow, free, info; done absent. Initial Telegram ConnectError resolved on retry. No task or calendar mutation needed.
- Next: none. Rollback reference chronos-00250-fiw; previous tags preserved.
- Commit/push: application pushed as 1c9cd43; completed deployment record prepared for documentation-only commit/push, verify Git tracking readback. No abandoned approach.
