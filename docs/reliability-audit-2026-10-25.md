# Chronos reliability audit — 2026-10-25

This is the initial local-only audit. See the [continuation and PR evidence](reliability-continuation-2026-10-25.md) for subsequent fixes and integration checks.

## Scope and evidence boundary

Work started at 18:37:45 Asia/Taipei from `7953322`, on
`codex/reliability-audit-20261025` in an isolated checkout. The parent checkout's
unrelated changes were preserved. The requested budget was about 90 minutes;
actual elapsed time is recorded in the handoff and final response.

Tests define expected state and permitted side effects before calling the real
service/router. AI results are synthetic; Calendar/Telegram are mocks or isolated
HTTP transports. SQLite uses disposable databases; Firestore uses its strict
transaction fake. No live model accuracy, production write E2E, live Firestore
transaction, deployment, or remote CI success is claimed.

## Reproduced problems and fixes

| Trigger / reproduction | Expected behavior | Observed before fix | Implemented behavior |
| --- | --- | --- | --- |
| Booked edit returns HTTP success but event remains unchanged, has wrong time/token/identity, or readback fails | Keep the old local booking/budget, retain uncertain operation, withhold success | Local time/budget changed and success was reported without readback | Read actual event identity, marker, aware times and status before recording success |
| Task completed or changed during classification, parsing, agenda read or PATCH | Reject before a write where possible; if remote write already occurred, retain reconciliation state | Old edit could move a different scope or acknowledge a completed task | Recheck classifier target, atomically reserve the current task/state, and recheck before the local receipt |
| Calendar/local commit succeeds, then the process stops before webhook receipt | Preserve and replay the verified acknowledgement without reclassification or another PATCH | The same update could PATCH again | Store a durable operation reply in the same task/budget transaction; restore the webhook receipt before routing. Recovery replies for a different update are also saved |
| PATCH outcome uncertain; next request asks for another time or cancels | Do not silently execute the older requested time | Different text could replay the old pending move | Pending request digest; a different request cannot write. Original request or explicit retry reconciles; old records support explicit retry |
| Backward insert response is successful but actual event is absent/changed/cancelled/unreadable | Preserve partial progress without claiming the whole plan exists | Only insertion response ID/marker checked | Read back time/title/identity/marker/status before recording each event |
| Partial plan's previously verified event is deleted before recovery | Stop for inspection, preserve tasks and remaining progress | Backward could recreate it; study reset could omit it and claim completion | Missing verified event stops recovery before further writes |
| `明天 5:00 PM` or `明天 17:00 UTC` | Preserve the structured parser's meridiem/zone conversion | Deterministic shortcut overwrote it as a Taipei 24-hour clock | Qualified clocks bypass that narrow shortcut; accuracy of the upstream provider remains a separate boundary |
| Availability begins at `14:15:30`, or a `UTC+14` window starts on the previous local date | Never start before the window/ten-minute lead; search the local date | Seconds rounded downward; foreign date could omit a feasible day | Ceiling alignment and normalized local search dates |
| UTC/offset deadlines with reversed ISO lexical order, equal instants, or DST fall-back | Correct chronological priority and stable ID ties | String ordering could put a later deadline first | Configured-timezone date priority and absolute-instant ordering on both stores; stored values unchanged |

## Coverage and acceptance criteria

| Area | Examples / required outcome | Evidence |
| --- | --- | --- |
| Colloquial input, omissions, typo, mixed English, multiple requests and contradictions | `報高晚點用`, `tmrw afternon`, incompatible duration/start constraints: unresolved provider question must block all mutations and preserve its question | Natural schedule route tests; these are execution-boundary contracts, not live linguistic evaluation |
| Reference and correction across turns | Ambiguous report completion asks which task; clarification uses fixed ID; expired/stale contexts cannot redirect an operation | Intent and ordered-sequence suites |
| Relative dates, midnight, month boundaries and timezone offsets | Existing relative/window fixtures; qualified Backward deadlines; strict lower bounds and cross-offset date handling | Time, Backward, natural schedule and slot-boundary suites |
| Repeated events, conflicts, ordered rescheduling, infeasible limits | Recurring/shared events remain protected; reviewed conflicts invalidate approval; sequence/deadline/capacity preserved; infeasible plans have no writes | Calendar replan, sequence, study revision/reset and Backward suites |
| Confirmation and completed-item preservation | No event writes during proposal; Cancel/stale Allow cannot execute; completed chapters stay unchanged; replay never creates duplicate events | Existing router/receipt/approval suites plus new recovery tests |
| Failures and interruption | HTTP 412, lost PATCH response, readback 503, no-op success, interruption before local receipt, missing verified events | New acknowledgement/recovery tests, both stores and real Calendar client over MockTransport |
| Independent scheduling comparison | Enumerate feasible grid points independently; compare earliest feasible result with the real search, including capacity/deadline/conflict/meal windows | 1,200 seeded cases over Taipei, UTC and New York, plus 300 lower-bound invariant cases; not a completeness claim |

## Validation and limitations

Actual elapsed: 32 minutes 45 seconds (18:37:45–19:10:30 Asia/Taipei); the approximate 90-minute budget was not fully used.

Final tree: 2,171 Python tests passed in 138.73 seconds; one existing Starlette deprecation warning. Extension: 21 passed. Ruff 0.14.1 undefined-name checks, compilation and diff checks passed. Documentation: 92 local links in 112 Markdown files, zero missing targets.

Initial complete
regression passed before later refinements, so that earlier run is not used as
final-tree proof. CI's Ruff 0.14.1 undefined-name check was available from the
existing offline cache and passed locally.

Extra Calendar GET calls and a Telegram-message checkpoint lookup are required for verified acknowledgements; no latency
or provider cost improvement is claimed. Calendar can still be edited externally
after any readback; the database and Calendar are not one atomic transaction.
Removed verified events deliberately leave a partial plan requiring inspection;
this audit does not automatically discard evidence or silently rebuild around it.

No commit was created because configured commit hooks can send Telegram
notifications. No push, PR, merge or deployment was performed. The changes remain
reviewable in the isolated checkout. Future publication should separate atomic
units (booking acknowledgement/recovery, Backward acknowledgement/time semantics,
slot bounds, and deadline priority) and obtain required CI before integration.
