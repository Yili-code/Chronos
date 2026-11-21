# Default acceptance for routine work-session planning

- Updated: 2026-10-24 (Asia/Taipei).
- Base: c50b296; branch codex/automatic-planning; isolated checkout .worktrees/automatic-planning.
- Authorization: owner explicitly requests default Accept without proposal display for planning. Ordinary new work-session booking may use standing authorization; moving other events, changing deadlines or exceeding working-hour policy remains an exception.
- Scope: default automatic task/work-session booking, scheduled receipts and post-booking adjustment, nightly queue and recovery copy, configurable manual mode retained.
- Design: reuse deterministic event IDs and existing booking reconciliation; validate snapshot, future time, deadline, workload and conflicts immediately before acceptance. Never send a successful receipt before verified Calendar creation. Uncertain receipt delivery is not automatically retried.
- Validation pending: both backends, default automatic and explicit manual modes, stale state/conflicts, multi-task queue, insert uncertainty/reconciliation, delivery failure, nightly and recovery flows, complete suite and remote CI.
- Commit/push: none. No deployment authorization for this new change. Next: implement, validate and publish PR.

- Implemented: learning_auto_accept defaults true; automatic_planning validates fresh snapshot/time/deadline/working hours/workload/conflicts before entering existing deterministic booking/reconciliation. Verified Calendar booking precedes a durable Scheduled receipt. Routine pending proposals, nightly sessions and explicit recovery replan choices use default acceptance; no routine proposal card is sent.
- Post-booking correction: Adjust time on the receipt or scheduled-plan details opens an explicit edit of that booked event through the existing verified reschedule path. Cancellation leaves the booking unchanged. Reflow/late exceptions retain the existing decision path; no-slot notices remain actionable.
- Tests: explicit manual-mode fixtures preserve all original approval safety assertions; new default-automatic tests exercise both backends, actual tick and webhook routing, queue progression, conflict/snapshot/deadline/budget rejection, uncertain insertion reconciliation, rejected/uncertain notification, exception preservation and nightly booking.
- Development validation: 106 focused tests passed. Initial complete suite: 1,701 passed and two failures (same batch-duration acknowledgement assertion on both stores). Fixed production acknowledgement to retain supplied minutes; did not weaken tests. Final complete rerun pending.
- Supporting checks: extension 21 passed; docs 86 local links / 84 Markdown files, zero missing; compilation and diff checks. No production config writes, live Telegram/Calendar changes or deployment.

- Final-tree validation: full Python suite 1,703 passed in 108.08s, existing Starlette deprecation warning only. Extension 21 passed; documentation links, compilation and whitespace checks passed. Final whitespace-only cleanup did not alter behavior.
- Default automatic and explicit manual-mode safety checks both retained. No model switch, disabled integration or weakened assertion. Preparing atomic publication; remote CI pending. No deployment performed or authorized for this feature.
