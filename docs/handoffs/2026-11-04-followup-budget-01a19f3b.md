# Bounded mail follow-up polling

- Updated: 2026-11-04 Asia/Taipei. Branch codex/executive-assistant-audit, base a99257647437a7a2b60045371c5cd86c98580352.
- Scope: bounded external waits with a durable fair cursor, timeout-safe read/delivery states and no replay of uncertain notifications. Tests use only synthetic mail and owner delivery mocks.
- Reproduction, implementation, verification and publication pending.

- Reproduced original unbounded provider wait on both database backends (2 failures). Implemented per-run 45-second external timeout and durable rotating cursor; manual one-thread checks preserve it. Deadline exhaustion defers remaining work. Timeout ending the current async context can precede monotonic comparison by timer precision, so a caught TimeoutError explicitly ends the run rather than starting another record.
- Failed-read markers now compare the captured status/baseline/deadline before mutation; they cannot taint concurrently resolved or extended waits. Delivery timeout can pause only its own unique notification claim; sending journals remain uncertain and never replay automatically.
- Focused validation: 189 passed including 16 new budget/recovery cases over both databases. Includes blocked provider, fairness, unknown delivery, manual cursor isolation, changed baseline/deadline/status, and successful read recovery. Full suite/build pending. No real mail or owner test deliveries.

- Prior meeting-bound correspondence commit a99257647437a7a2b60045371c5cd86c98580352 CI 41781082520 verified Python 3.11, Python 3.12 and extension success. No merge or production deployment.

- Final validation: 3,870 passed, zero failures/errors/skips, 142 existing warnings; 193.20 seconds wall output. Release wheel matches runtime source. Ruff undefined-name, docs links and diff checks passed. Evidence: docs/evidence/mail-followup-budget-2026-11-04.json. Commit/push and CI pending at this entry; no merge/deployment.
