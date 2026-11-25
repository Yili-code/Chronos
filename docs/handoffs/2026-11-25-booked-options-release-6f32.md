# Booked work-session adjustment release

- Updated: 2026-11-25T15:28:40.4880255+08:00
- Base: 9fc84e3b30b83aa0ad4c8ee29ae8ce9d9bc8f16b, branch codex/booked-options-release.
- Scope: release the previously prepared booked-session calendar preview and slot selection. Applied the existing patch in a separate worktree. Original checkout and uncommitted material are preserved.
- Verified: live Git refs match local tracking refs. Python 3.12 asyncio probe, 21 extension tests and documentation link checks passed. Full local Python suite passed with 4711 passed and 1 skipped. Python source compilation and git diff --check passed.
- Production before release: chronos-calendar-personal-1121 carries 100% traffic. Its release environment reports 1964baa and its historical labels identify 14a9baa, f8746cd and d7e114b. All four are ancestors of current main.
- Pending: full Python results, commit and push, PR and merged SHA CI, candidate readiness, traffic promotion and live version verification.
