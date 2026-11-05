# Executive assistant acceptance audit

## Original drafting-language slice (2026-11-05)

[Original-request evidence](evidence/original-drafting-language-2026-11-05.json) preserves the exact input and expected behavior of `DRAFTING-01`, `02`, `04` and `09`. It materializes ambiguous threads, public versus internal negotiation facts, contradictory owner delivery instructions and forged email authorization. Three bound cases use the configured live model; the unbound ambiguity case is correctly resolved at the actual application boundary without a model call. All Gmail/Calendar/Telegram boundaries are synthetic. Recorded model inputs/results, actual replies, API calls and DB states distinguish this from the workflow-double catalog below.

The first live execution had three passes and a failure: contradictory delivery instructions produced a contradictory draft. The repaired execution passed all four unchanged expectations. A factual semantic rubric supplements the decline case; identity, canary disclosure, tool effects and DB state are independently checked by code. Source hashes and both observations are retained. Drafting cases `03`, `05`, `06`, `07`, `08` remain **NotExecuted** in this slice; `10` remains owner-deferred. Other original domains are not evaluated by this slice. The full original80-case acceptance remains incomplete; no expectations were rewritten or unexecuted cases counted as passes.

Reproduce with `python scripts/audit_original_drafting.py --output .pytest-tmp/original-drafts.json --basetemp .pytest-tmp/original-drafts-run`. This uses schema-aware model doubles by default; `--live` explicitly uses configured AI environment credentials, with application services initialized from safe defaults and external writes mocked. CI publishes the mock replay alongside existing exact-source evidence. Live results do not establish real-recipient or individual Production case completion.

## Current evidence supplement (2026-11-04)

Selected-thread composition adds ten **additional** executable definitions (`COMPOSITION-01` through `COMPOSITION-10`) without changing the prior definitions or original acceptance expectations. Current CI observation reports therefore contain 90 definitions: 80 active workflow definitions and ten deferred messaging definitions. The stored 80-definition snapshot linked below remains its original dated snapshot. Selected search-result cards can now atomically prepare a meeting candidate, draft and tracking, with timed candidate revalidation before native draft saving/sending; automatic search-result selection and full original natural-language acceptance remain incomplete.

The baseline below is preserved unchanged; its missing-feature descriptions are historical. Reviewed drafting/sending, local contacts/relationships, native invitations/RSVP, rescheduling, explicit reply tracking and bounded natural draft/tracking workflows were added later. Read-only owner-query search is documented in [Gmail](gmail.md). Single configured Gmail remains the active scope; LINE/Instagram and extra Gmail accounts remain deferred.

The [observed workflow catalog](evidence/secretary-workflows-2026-11-04.json) contains 80 independent scenario definitions: ten per requested domain plus ten cross-domain scenarios. Seventy executable workflow definitions use actual service commands and behavioral assertions against synthetic SQLite/Firestore and API/model doubles; ten platform definitions preserve the owner's deferral. Parameter variants are executions of their parent definition, not additional independent cases. Every execution records commands, replies, API arguments/results, ordered DB transitions, final states and failed assertions if present. Source hashes bind the evidence to actual files.

Reproduce with `python scripts/audit_secretary_workflows.py --output .pytest-tmp/secretary-workflows.json --basetemp .pytest-tmp/secretary-workflows-run`. CI repeats this observation on each validated commit and uploads its exact-source report alongside the normal test evidence. The original 80-case corpus and expectations are not changed or retroactively regraded. This supplement is bounded workflow evidence; it does **not** certify arbitrary original natural-language requests, cross-platform identity resolution, Google Contacts sync or real-recipient Production execution. The full original acceptance gate remains incomplete.

Current support: email search/triage is bounded to one verified Gmail; reply drafts have explicit local/native/send states and reviewed recipients; contacts/relationship data are owner-maintained locally; meetings verify private holds, native invitation/RSVP and in-place rescheduling; follow-ups distinguish owner/recipient waits and pause unknown/automatic outcomes. General search-to-meeting orchestration and unsupported third-party platforms must not be reported as completed.

Date: 2026-11-03, Asia/Taipei. This is an incomplete acceptance audit, not a claim that Chronos now provides all requested secretary functions.

Baseline scope: the capability table and 80-case results below describe the original audit at commit `6effb2d`. Subsequent reviewed mail draft and reply-tracking implementation is documented in [Mail correspondence](mail-correspondence.md); it does not retroactively turn the baseline's blocked scenarios into passes.

Local contact/relationship functionality is separately documented in [Contact relationships](contact-relationships.md). It does not imply Google Contacts authorization or change the baseline's original outcomes.

Reviewed meeting candidates and private tentative calendar holds are documented in [Meeting coordination](meeting-coordination.md). They are not attendee coordination or invitation delivery.

Later [explicitly authorized Gmail reply sending](authorized-mail-send.md) adds a separate per-message send confirmation. Baseline claims of no send transport below are historical, not a description of this later implementation.

[Combined validation evidence](evidence/secretary-integration-2026-11-03.json) records integration with the independently published main baseline, locked dependencies, actual full-suite results and current read-only Production observations. These observations do not mean PR124 was deployed.

## Scope and capability evidence

| Domain | Current owning implementation | Supported behavior and remaining gap |
| --- | --- | --- |
| Email | `chronos/gmail.py`, `chronos/mail_workflow.py`, mail-card routing in `chronos/main.py` | Single configured Gmail account; bounded message listing, decoding, summaries, conservative automatic trash, owner-card archive/read/trash and task creation. No general multi-account search, arbitrary labels, full-thread analysis or attachment processing. |
| Reply drafting | Gmail transport and mail workflow | No email reply-draft or send endpoint. Existing `draft_title` is a proposed **task title**, not a Gmail draft. |
| Cross-platform messaging | No LINE/Instagram application adapters | User deferred this capability on 2026-11-03. Dependent scenarios are excluded from active acceptance. |
| Contacts | No contact adapter, identity/merge model or owner workflow found | No contact CRUD, conflict resolution or relationship identity store. Desktop connector access is not an application integration. |
| Meetings | `chronos/google_calendar.py`, learning-calendar modules | Calendar availability, agenda, search and managed event writes exist. Writes use `sendUpdates=none`. These are not attendee negotiation, invitation delivery or attendee-confirmation tracking. |
| Relationships | No relationship/preferences/interactions model found | Existing course/task progress tracking does not provide contact relationship management. |
| Follow-ups | Mail task creation and learning/task reminder modules | Can create an owner task from a mail card. No email-thread reply-state ingestion or automatic cancellation after the other person replies. A task reminder is not proof of reply tracking. |

The Gmail consent helper requests `gmail.modify`. The separate calendar consent helper requests `calendar.events` and `calendar.readonly`. There is no Contacts authorization flow. Current scope/grant validity for new functionality was not verified. No additional OAuth consent, real-recipient sends, mailbox writes or production configuration changes were performed by this audit.

LINE's official [Messaging API overview](https://developers.line.biz/en/docs/messaging-api/overview/) describes messages sent to an Official Account and its webhook, not arbitrary access to a personal inbox. LINE/Instagram integration is deferred, rather than substituted with browser scraping or assumed platform capabilities. The Meta documentation endpoint returned HTTP 429 during lookup; no live Instagram authorization claim is made.

## Reproducible catalog and measurements

The [machine-readable report](executive-audit-results.json) contains 80 independently identified cases: 10 email, 10 each for drafting, messaging, contacts, meetings, relationships and follow-ups, and 10 cross-domain scenarios. Each record includes original input, preconditions, expected outcome, actual response/component result, observable calls, API results, database effects, discrepancy, outcome, cause, remedy and regression status.

Reproduce from the repository root with its development Python environment:

```powershell
python scripts/audit_executive_assistant.py --defer-messaging --output docs/executive-audit-results.json
```

The audit intentionally exits nonzero (2) when acceptance is blocked. Do not use the command's output or an ordinary pytest green result as release authorization.

| Outcome | Count | Meaning |
| --- | ---: | --- |
| Pass | 10 | Decoder-to-classifier component checks only; original natural-language input is a scenario description, not executed by an intent model. |
| Fail | 0 | Current executed component/fallback probes had no unexpected state changes. This does not establish missing features work. |
| Blocked | 54 | Missing requested workflow; only actual mail-card fallback and absence of side effects were probed. Scenario-specific external data was not materialized because the corresponding adapters/models do not exist. |
| Deferred | 16 | LINE/Instagram and dependent cases excluded by the user's scope decision; not executed. |

Catalog component pass fraction: 10/80 = 12.5%. Excluding deferred cases: 10/64 = 15.625%. Neither is an end-to-end functional pass rate. End-to-end passes established: **0**. Automated safety tests passing is distinct from product acceptance; no blocked scenario is counted as a success.

## Confirmed defects and repair

1. **High — automatic trash based on incomplete evidence.** Filename attachments, remote body parts, unreadable Base64/UTF-8, calendar/encrypted MIME and attached messages were omitted from the classification evidence without a completeness signal. Nine deliberately different classifier cases selected trash before repair. The decoder now sets `body_incomplete`, and the filter retains incomplete or truncated mail before either promotion or explicit account-security rules. Ordinary complete promotions retain existing cleanup behavior. No mail is permanently deleted by this transport.
2. **High — attached message content entered summary input.** A `message/rfc822` MIME tree without a filename was traversed as ordinary body text despite the decoder's attachment boundary. A dedicated regression failed before repair. Such MIME subtrees are now skipped and marked incomplete. Mail cards disclose that unread content/attachments are outside the summary. This is not attachment reading support.

Pre-repair evidence: 10 failed, 1 passed across the initial 11 new regression checks. The initial 10 failures are nine unsafe classifications plus one attached-message boundary violation, not ten independent implementation defects.

The schema change is an optional field in existing JSON mail state; no SQL/Firestore migration is required. Old records without it remain readable. Daily processing obtains fresh decoded messages before classification. Explicit owner-card operations retain their existing authorization and account-verification checks. Existing deliberately configured security-notification cleanup remains active for complete evidence.

Regression includes both SQLite and Firestore fake: fresh incomplete mail is not trashed, archived or marked read, while the retained card is delivered and journal/binding state is saved. These are mocked transports and persistence-fake evidence, not Gmail/Telegram live delivery evidence.

## Validation and release gate

- Initial affected-suite regression: 86 passed before the final card-warning/integration tests were added.
- Initial full Python regression: 3012 passed, 1 Starlette/httpx deprecation warning; this run preceded the final warning/integration changes and is not final-version evidence.
- Final mail-evidence regressions: 14 passed, including both persistence backends.
- Final full Python regression: **3013 passed**, 1 existing Starlette/httpx deprecation warning, 225.38 seconds.
- Chrome extension: 21 passed, 0 failed.
- Undefined-name checks and Python compilation passed. No repository type-check configuration was found or introduced.
- Final documentation check: 99 local links in 138 Markdown files, 0 missing. Diff whitespace checks passed.
- Build: `uv build` successfully produced the source archive and wheel in an isolated build environment.

Live read-only baseline: `chronos` in `asia-east1`, revision `chronos-gmail-enable-1103`, 100% traffic, source `3ef3b0077aeee72335f1cd0c21ae07b1ba7428af`, Gmail flag true. `/health` returned HTTP 200 and `{"status":"ok"}`. This endpoint does not validate Gmail, Telegram, Contacts, Calendar or the new patch. No post-deployment smoke test occurred because this task has not deployed.

**Release gate is not met.** The requested active core domains still have unsupported workflows. New drafts/contact changes/invitation state must not be claimed as completed. The mail safety patch can be reviewed independently, but this audit does not authorize treating the entire executive-assistant request as accepted. No merge or production deployment while the user's core-acceptance condition remains unmet.

## Remaining implementation requirements

Full-thread reply drafting needs explicit recipient/CC/BCC/attachment validation, grounded content and separate local/native-draft/send states with uncertain-outcome handling. Contacts need an authorized provider, provenance, conflict/version checks and conservative identity resolution. Meeting coordination needs attendee state and explicit invitation delivery semantics on top of the existing calendar adapter. Relationships need preferences and sourced interaction history; follow-ups need reply classification, cursor recovery, refusal/auto-reply handling and dispatch-time state reconciliation. These are missing application implementations, not merely unprovided credentials. They remain unresolved in this change.

Production rollback and monitoring for a future release must be checked at that release's immutable SHA. Existing revisions and today's healthy baseline do not prove a future image or data-state rollback safe.

### Original draft state slice (2026-11-05)

Unchanged DRAFTING-06 is now executed with a newer offer after draft creation. The deterministic controller must read the full thread, invalidate the cached local draft, withhold the old body and require new review; no generation or native write is authorized. The reproducible original-drafting runner now executes five definitions (01/02/04/06/09) across nine backend executions using model/external doubles:5 Pass,4 NotExecuted,1 owner-deferred. Prior actual-model evidence for01/02/04/09 remains separately dated; this update does not claim a fresh live-model run or Production case execution. See [state evidence](evidence/original-drafting-state-2026-11-05.json). Full original80 acceptance remains incomplete.
