# Reliability continuation and integration evidence

Started: 2026-10-25 22:40:47 Asia/Taipei. This continues the
[initial local audit](reliability-audit-2026-10-25.md), preserving its fixes and
all unrelated parent-checkout work. Eleven atomic implementation/verification commits
are based on main `2302d21`, including the newer study/activity rebuild commands.
No merge, deployment or production configuration change is authorized or performed.

## Main findings

| Reproduction | Before | After |
| --- | --- | --- |
| Press Edit, then the old Allow before a replacement exists | Original window/sequence arrangement could still execute | Edit immediately revokes the old approval; Cancel remains available |
| Submit replacement constraints while the model is unavailable | Old pending Allow remained executable after parsing failed | Approval is revoked before external parsing; failed parsing cannot restore it |
| On Sunday, request `明天（星期二）下午兩點，做一小時` | The real model chose Tuesday and the local booking path applied it | A shared literal date/weekday check asks which assertion to use, independently of model compliance |
| Correct a Backward deadline in a later turn | Deterministic extraction could reuse an old relative date from concatenated history | Only the current turn can supply a deterministic override; retained context remains available to the model |
| Externally turn a managed booking into a shared event, recurring series/instance, special event or unverifiable owner event | Single-booking edits trusted Chronos markers and could PATCH/acknowledge it | Single and ordered edits share the same owner-only, non-recurring boundary before writes and during readback |
| A pending edit survives beyond the old session end | The background worker could advance the old booking into feedback and block edit recovery | Keep pending edit state unchanged until explicit reconciliation |
| Restart after PATCH before the webhook receipt, then let classification select another task | Retry could execute against that other task | Persist the original task/token before PATCH and resume it before any model routing; an active writer yields retryable HTTP 503 without a misleading receipt |

The initial fixes are also included: readback-based acknowledgements; task snapshot
checks; durable booked-edit replies across the post-commit/pre-webhook-receipt gap;
request-bound uncertain retries; missing verified-event recovery protection;
AM/PM/UTC preservation; upward slot alignment; local-date window search; and
chronological deadline priority across offsets.

## Evidence boundaries

- **Local application tests:** actual routers/services, disposable SQLite and strict
  Firestore fake. Controlled failures verify task status, event content, budget
  buckets, pending operations, replay receipts and mutation counts. New ownership
  tests fail before the fix for both pre-write changes and post-write readback races.
- **Real model:** ten multi-turn scenario types exercised with synthetic context.
  The first four passed on both stores; the remaining six initially encountered
  HTTP 429. Paced SQLite retests passed those six. Two representative scenarios
  were rerun successfully after integrating the newer main classification schema.
- **Real model plus local execution:** seven fixed temporal cases passed execution
  acceptance, including mixed language/typo, missing reference, self-correction,
  UTC cross-month conversion, date/weekday contradiction and an ambiguous DST
  local time. Calendar and Telegram are mocked in these cases. A 17:00 PM request
  correctly remains unchanged because dinner is protected. The initial expectation
  incorrectly assumed it could be booked; no product limit was weakened.
- **Model limitation:** the provider still returned an actionable Tuesday result in
  a post-fix contradiction sample. The independent application guard prevented
  the move and asked the necessary question. These results do not certify general
  language understanding. Captured model outputs also passed offline replay after
  the final ownership hardening; this replay is not a new live-model call.
- **Real Telegram service:** the existing private chat was verified; one clearly
  marked test message was sent, edited and deleted successfully using TelegramClient.
  API acknowledgement is verified; no receiving-screen screenshot is claimed.
- **Calendar:** inventory was read through the connected service; no dedicated test
  calendar was identifiable. The local product OAuth refresh token was rejected.
  An attempted production credential read was rejected by automatic approval review
  before execution; it was not retried or bypassed. No real Calendar write test ran.

Sanitized observations are in
[the evidence record](audits/2026-10-25-reliability-live-evidence.json). Synthetic
inputs are fixed in `tests/fixtures/reliability_time_cases.json`.

## Reproducible model verification

The opt-in tool sends only the fixed synthetic inputs. It runs the actual local
booking service with mocked Calendar/Telegram and removes its disposable databases
on exit. It requires a minimum ten-second call interval. It never changes a webhook,
Calendar event, production setting or private task database.

```powershell
python scripts/verify_reliability_model.py --live --env-file <local-env-path> --output .tmp/model-evidence.json
```

`--live` is required; do not paste credentials into chat or commit environment files.
This command does not validate real Calendar writes.

## Validation and cleanup

Final local results and draft PR/CI status are recorded in the
[continuation handoff](handoffs/2026-10-25-reliability-continuation-01a172a0.md).
Final local validation: **2,320 Python tests passed in 158.17 seconds**, with one existing Starlette deprecation warning. Extension: **21 passed**. Ruff 0.14.1 undefined-name checks, source compilation and diff checks passed. Documentation: 98 local links in 117 Markdown files, zero missing targets. Application commit `351f409` was the frozen code under this final local run. Required draft PR CI remains a separate publication gate.
Ruff 0.14.1 undefined-name checks, compilation and extension checks are included.

The marked Telegram probe was deleted. Seven owned scratch database files were
removed; reproducible-run databases clean themselves on exit. No test Calendar
objects were created, so none require deletion. Synthetic logs/evidence remain
local or in the sanitized record. Normal authorized Sisyphus commit notifications
are development records, not disposable test messages.

## Remaining limits and next step

Real Calendar write E2E requires an empty dedicated test calendar and a renewed
local Chronos Google authorization. Existing private/family events must remain
untouched. A missing previously verified event deliberately pauses a partial plan
for inspection. Uncertain booked edits pause background lifecycle advancement until explicit reconciliation; an elapsed original slot or externally changed event may require owner inspection rather than automatic repair. Calendar may change after readback, and there is no atomic
transaction across Calendar, the application database and Telegram.

The draft PR must pass both Python versions and extension CI. Do not merge or
deploy as part of this task. No elapsed-time or test-count claim of comprehensive
reliability is made.
