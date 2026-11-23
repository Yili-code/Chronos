# TronClass no-new-assignment notice

- Updated 2026-11-23 (Asia/Taipei). Base 933cb42da488947da2f428a4498eb4bfa4c7f5c4, branch codex/tronclass-read-7s.
- Scope: chronos/assignment_scan.py and tests/test_assignment_scan.py. Only the first valid acknowledgement of the bound notice starts its seven-second expiry. Repeated clicks retain the original deadline.
- PR #186 previously set this interval to three seconds. Validation, publication, merge and release verification are pending.
- 2026-11-23 17:40 +08:00. Independently fetched origin and verified main at the base above. Full local Python 3.12 suite: 4710 passed, 1 skipped. All 21 extension tests passed. Python compilation, documentation links and diff checks passed. Callback regression covers an unrelated message ID and two valid clicks with a single deadline.
- Publishing this change for required PR checks. Verify the merge SHA's own workflow and the deployed service before reporting completion.
