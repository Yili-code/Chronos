# Executive assistant audit — 2026-11-03

Registration was removed from product scope by the owner during this audit.
This report describes verified capabilities, not certification that every
possible executive-assistant workflow exists.

## Capability coverage

| Domain | Implemented and tested | Limits |
| --- | --- | --- |
| Calendar | Owner Calendar transport, multiple read calendars, expanded recurring occurrences, deterministic managed identity, conditional edits/deletes, safe reconciliation | No general cross-provider calendar federation or arbitrary recurring-series editor; attendee/foreign/recurring moves remain protected |
| Intelligent reminders | Durable owner-scoped one-shot notifications, overlap approval, cancellation, rejection retry, uncertain-delivery handling; deadline hour/day stages | No inferred importance or travel time; an uncertain external send cannot be proved delivered or safely resent automatically |
| Conflict resolution | Busy coverage, fixed classes/preparation/meals, approved holds, flexible owner-only previews and conditional application; nested overlap reporting | No unauthorized move, attendee notification or generic travel-buffer inference |
| Daily briefing | `/brief` and natural intent: current Calendar, conflicts, open tasks, deadlines, unspecified/uncertain times; existing daily scheduler uses durable per-part receipts when Calendar enabled | Snapshot at generation; `/brief` refreshes data after subsequent changes; delivery and external writes are not one distributed transaction |
| Weekly planning | `/week` and natural intent: next Monday–Sunday, events, usable blocks, holds, backlog estimates, missing estimates, overload and overlap warnings; Monday daily job includes report | Suggestions are read-only; missing estimates remain unknown; free blocks use existing work/class/meal policy, not invented preferences |
| Event registration | Removed by owner | No registration/payment/submission/organizer contact integration; normal task capture does not imply registration success |
| Deadlines | Date-only vs explicit timestamp, timezone conversion, distinct day/hour stages, postpone/remove/complete handling, durable delivery identity | No invented midnight cutoff; hard deadlines remain separate from scheduled work and uncertain time |
| Time usage | `/time` and natural intent: previous seven complete local days, union of occupied intervals, explicit Calendar-linked actual-duration samples, deduplication and coverage disclosure | Self-reported sample total is not all actual work; unlinked work, category breakdowns and long-term trends remain unknown |

## Reproducible evidence

- [Per-case inputs, expected behavior, actual replies/tool results, backend, differences and outcomes](executive-audit-cases-2026-11-03.json).
- [Executable 80-case corpus](../tests/test_executive_adversarial.py): 70 active cases, ten per remaining domain, plus ten excluded registration refusal boundaries. Interpreted inputs are synthetic and service-level; these **do not establish that a live model understood all 80 inputs**.
- [Actual model request captures](../tests/fixtures/executive_report_model.json) and [webhook replay](../tests/test_executive_model_contract.py): nine distinct Chinese/English requests used the production-configured provider with disposable SQLite and isolated Calendar/Telegram, then replayed on strict Firestore fake. No owner scheduling or Telegram writes.
- [Report routes and receipt retries](../tests/test_executive_report_routing.py), [scheduled delivery](../tests/test_executive_scheduled.py), [Calendar contracts](../tests/test_calendar_response_contract.py), [reminder claim races](../tests/test_reminder_claim_changes.py), [DST/Unicode](../tests/test_report_time_boundaries.py).
- Generate case evidence with `python scripts/export_executive_audit.py --junit <JUnit path> --output <JSON path>` after pytest with `-o junit_family=legacy`.

The safety refusal cases use an explicit unsupported classifier result. They
verify the deterministic refusal path and absence of external effects, not a
live prompt-injection or registration implementation claim. Policy tests of
protected events inspect actual move eligibility; they are not proof of a live
conversation completing a complex reschedule. Existing command-reliability
tests separately replay previously captured provider results through the real
webhook and assert database/Calendar state.

## Confirmed defects and repairs

| Severity | Root cause | Repair and regression evidence |
| --- | --- | --- |
| High | Missing `busy` data was treated as an empty schedule; malformed time values escaped the provider contract | Require every requested calendar and explicit busy list, valid aware positive intervals; reject incomplete coverage. Before repair Calendar regressions: 15 failed, 1 passed; after repair all 16 pass |
| High | Reminder used the pre-claim snapshot after cancellation/payload change and did not filter dispatch by configured owner | Re-read live claim/status/payload immediately before send, filter owner, preserve cancellation, surface corrupted claimed data as uncertain. All eight both-store regressions failed before repair and pass after |
| Medium | Calendar agenda pagination could loop forever on repeated provider token | Reject token cycles and stop after 40 pages without returning a partial agenda |
| Medium | Event bounds accepted timezone-free provider timestamps by applying the machine timezone | Reject ambiguous/nonpositive event bounds; valid explicit offsets preserved |
| Medium | Wall-clock subtraction misreported elapsed time across DST; Python character counts undercounted Telegram UTF-16 size | Use UTC elapsed minutes and UTF-16 output bounds; spring-forward/fall-back and lossless long-Unicode tests |
| Critical/High runtime and Moderate development advisories | Locked `certifi` trust bundle, `pyasn1` ASN.1 decoders and pytest temporary-directory handling had published advisories | Require patched runtime/test releases; lock resolves certifi 2026.10.23, pyasn1 0.6.5 and pytest 9.2.0; audit of 51 installed packages reports no known vulnerabilities |

Primary references: [Calendar FreeBusy contract](https://developers.google.com/workspace/calendar/api/v3/reference/freebusy/query),
[Calendar pagination](https://developers.google.com/workspace/calendar/api/guides/pagination),
[certifi maintainer advisory](https://github.com/certifi/python-certifi/security/advisories/GHSA-6q48-6c7w-9gw2),
[pyasn1 maintainer advisories](https://github.com/pyasn1/pyasn1/security/advisories),
[pytest security fix](https://docs.pytest.org/en/9.0.x/changelog.html#pytest-9-0-3-2026-04-07).
These establish provider/security contracts; local test counts come from actual execution.

## Validation and release

Baseline: origin/main `07866f6`; original-source full suite 2,999 passed.
Initial expanded suite: 3,194 passed before final security upgrade/model replay addition.
Final locked-source validation, PR/merge CI and deployed revision are recorded in
the [task handoff](handoffs/2026-11-03-executive-audit-01a19f37.md).
An earlier run is never evidence for a later commit or container.

No schema migration is needed: new reports use existing JSON action-state
records with independent keys; existing tasks and receipt schemas are unchanged.
Rollback uses the retained previous Cloud Run revision. Old code ignores new
report keys; an interrupted `sending` report stays uncertain on the new code
instead of being blindly resent. Existing credentials, Gmail activation,
Scheduler configuration and runtime settings must remain unchanged.

Production validation is read-only: readiness/version, authenticated component
status, actual Calendar read, unchanged settings/traffic guard and error logs.
`GET /internal/reports/{brief|week|time}` requires the existing scheduler secret,
returns `Cache-Control: no-store`, and does not send notifications or save report
receipts. Unknown reports return 404 and unavailable Calendar data returns 503.
Real scheduled delivery, visual Telegram rendering and owner-authorized
Calendar mutation E2E remain distinct from mock/receipt evidence.

## Review criteria for unautomated behavior

Check that uncertain dates stay uncertain, proposed changes remain proposals,
and reported completion agrees with durable state. Require independent provider
readback for external writes; never accept reassuring wording as evidence.
Unknown measurements remain unknown. A refreshed report must use current data;
receipt replay must not repeat mutations or already acknowledged parts.
