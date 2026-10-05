# Phase 2 — development resumed, acceptance incomplete

## Exam query pagination — 2026-10-05

`/exams [page]` now renders stable, lossless pages with next-page instructions.
Tests concatenate every page back to the complete source text and check UTF-16
length using supplementary Unicode characters. Save acknowledgments are bounded
instead of echoing arbitrarily long course/exam names. Both database backends
are covered; 66 focused exam and system tests passed. Delivery-plan consistency
under edits and production acceptance remain outstanding.

## Exam-week scheduler integration — 2026-10-05

Added one confirmation request per official exam period, eligible from seven
days before its start with catch-up until the period begins. During the period,
08:00 daily notices include only owner records explicitly dated that day.
Missing daily arrangements are labeled unknown, never interpreted as no exams.
Fresh official-calendar evidence is required. Durable keys prevent duplicate
sends and uncertain delivery stops subsequent parts. Long record messages are
split into bounded parts without dropping their text.

64 focused exam and system tests passed offline. Not yet deployed. Remaining
exam work includes long-list query pagination, consistent delivery snapshots
when owner records change mid-send, and live acceptance.

## Exam record foundation and regression evidence — 2026-10-05

Added explicit owner `/exam` input and `/exams` inspection with SQLite/Firestore
persistence. Course plus exam name identifies the record; include year/term in
the exam name to distinguish future sittings. Unknown date, location, scope and
review fields remain null and render as `尚未提供`; no AI inference is involved.
Exam-week confirmation prompts, daily messages, long-list pagination and live
acceptance remain outstanding. These commands are not yet deployed.

The full regression run started before these exam changes completed with
448 passed and 525 dependency deprecation warnings in 152.73 seconds. That
result covers the calendar integration, not the newly added exam commands.

## Calendar owner notices — 2026-10-05

The integrated scheduler now reports exhausted calendar-refresh attempts once
per Taipei day. Transient attempts remain silent. Ambiguous dates produce a
date-keyed request to confirm with the teacher, not a holiday claim. A closure
observed before 08:00 without a confirmed previous-day notice triggers an
immediate notice and retains the scheduled 08:00 reminder. All these sends use
the durable ledger. The confirmation reply workflow and exam information
management are still outstanding; these changes are not yet deployed.

## Calendar connected to scheduler source — 2026-10-05

The scheduler now refreshes calendar evidence, evaluates holiday notices, then
evaluates course prompts. Current explicit closures and exam periods suppress
both ordinary prompts and their reminders; pending sessions still receive
next-day lifecycle cleanup. Normal instruction, unspecified dates and ambiguous
events do not establish a closure. Stale data is returned as `unverified` and
does not suppress prompts. The protected endpoint integration is tested with a
mock refresh and Telegram; this is not evidence of production deployment.

Still required: owner-facing stale/ambiguous-calendar health notices, early
late-discovery holiday handling, exam confirmation/details and morning exam
messages, plus live deployment acceptance. Previous component-only notes below
describe their historical state before this source integration.

## Durable daily calendar synchronization — 2026-10-05

Added a daily refresh component backed by atomic SQLite/Firestore claims. A
current snapshot prevents another fetch that day. Failed or interrupted attempts
are bounded to three per Taipei day, separated by at least 30 minutes. Failed
fetches/parses preserve the last evidence without labeling it current. The source
is fixed, redirects are rejected, TLS verification remains enabled, and decoded
response size is capped at 1 MB. Error bodies are not stored.

Tests use mocked public HTTP and both database backends, including concurrent
ticks. This component is not yet invoked by the production scheduler; production
calendar activation and complete section-7 acceptance remain outstanding.

## Holiday delivery component — 2026-10-05

Implemented an offline-tested holiday notification planner and durable sender.
Only a same-Taipei-day, non-future official snapshot and an unambiguous `no_class`
classification permit a notice. Previous-day noon and holiday-day 08:00 windows
catch up when fresh evidence arrives later that day; past holiday dates are not
replayed. Date/window keys survive refreshes and process restarts. Unknown send
outcomes are not blindly retried; explicit rejection follows bounded ledger retries.

This component is not wired into production. Daily source synchronization,
early-morning late-discovery policy (before 08:00), course-prompt suppression,
calendar-health reporting and live acceptance remain open. It does not establish
completion of PRD section 7.

## Full-PRD objective resumed — 2026-10-05

The owner explicitly requested completion of the whole Study Module PRD. This
supersedes the development pause below, but not source-grounding, free-tier,
explicit file selection or secret-safety gates. Historical probe permissions
with consumed request budgets are not automatically renewed. No new live AI
success is established by this scope decision.

Current requirement audit from source inspection:

- Sections 2–3: seven-course scheduling, two reminders, reply correlation and
  survey/review tasks are implemented; production evidence is in Phase 1 status.
- Sections 3–4: browser-observed partial PDF catalog, explicit multi-selection,
  PDF persistence, per-segment draft/review, canonical notes and export exist.
  AI content-quality acceptance and live owner export acceptance remain open.
- Sections 5–6: periodic upstream monitoring, announcement/video notifications,
  assignment discovery, deadline confirmation, assignment reminder lifecycle and
  `/prepare` are not wired into the production application.
- Section 7: daily official-calendar synchronization, holiday suppression and
  notifications, exam confirmation and exam-day messages are not implemented.
- Section 8: no paid fallback is intended; persistent daily request/token budget
  accounting and near-quota warnings still need implementation and verification.
- Sections 9–10: delivery/update deduplication exists for current paths, but new
  assignment/calendar paths and separate integration-health reporting need work.
  Production health alone cannot establish full acceptance.

Completion remains unproven for the full objective. These gaps are not a reduced
scope or a declaration that scaffolding completes a feature.

First Phase 3 implementation slice: `chronos.assignments` now defines stable
course/source identity, explicit deadline provenance, missing-deadline state,
four reminder windows, deadline revisions, and completion suppression. Eleven
pure offline tests pass. A late discovery selects only the current reminder
window instead of sending a burst of historical thresholds. This module is not
yet connected to durable assignment storage, TronClass discovery, Telegram or
`/prepare`; it is not a completed assignment feature or production evidence.

Follow-up: SQLite and Firestore repositories now atomically create an assignment
record and its ordinary task, keyed by course/source identity. Duplicate discovery
returns the original task; ordinary task completion also persists assignment
completion history, including when the task list is later cleared. Eight new
storage tests pass on SQLite and a Firestore fake; the focused suite totals 52
passing tests. This supersedes only the missing-storage statement above. Source
updates, owner deadline confirmation, durable reminders, discovery integration
and `/prepare` remain unfinished. No production assignment data was written.

Next integration slice adds `/deadline` with explicit Taipei date/time input,
atomic assignment/task deadline updates inside the webhook receipt transaction,
and assignment discovery/deadline notifications through the durable delivery
ledger. The existing protected study tick calls the assignment scheduler.
Thirty-two focused offline tests pass, including unknown-delivery suppression,
missing-deadline prompting, duplicate receipts and completion suppression.
This has not yet been deployed or tested with live assignment discovery. Browser
ingestion, source updates, `/prepare`, and production acceptance remain open.
Generic task rescheduling still requires review so it cannot diverge from the
assignment deadline; the explicit `/deadline` path is the integrated path.

Deadline consistency follow-up: ordinary task postponement and editing now
update the linked assignment deadline transactionally on both backends. Removing
a deadline restores `deadline_pending`; changing it advances the reminder
revision, while an unchanged date does not. Personal task titles do not overwrite
upstream assignment metadata. Forty-three focused tests pass. This supersedes
the generic-rescheduling gap above; production and browser ingestion remain open.

Live browser evidence — 2026-10-05: the connected Chrome session was authenticated.
The Operating Systems homework list explicitly reported no assignments. A second
tracked course exposed a homework detail with a numeric activity path, a dedicated
title element, teacher-description container, explicit activity end timestamp,
an attachment reference and a submitted-status banner. No submission, edit,
history inspection or attachment download was performed. This is evidence for
the current detail-page DOM, not complete enumeration or future parser stability.

The extension now has a read-only `chronos.observe_assignment` extraction handler
based on those observed DOM selectors. It returns only allowlisted text/identity,
explicit deadline, conservative submission status and `attachments_status:
not_observed`. Five synthetic Node tests pass, covering missing/invalid dates,
unsupported pages and unknown submission state. It is not yet handed off to the
local/production assignment store; the installed extension has not been reloaded
or live-tested with this new code. Preserve this acceptance boundary.

Local handoff implementation now includes an explicit popup action, the existing
origin-checked loopback receiver's `/v1/browser-assignment` endpoint, and durable
SQLite observation snapshots with receipt timestamps. Nineteen focused tests pass,
including an actual loopback HTTP test, wrong-origin rejection and restart readback.
Snapshots preserve submitted/unknown status and do not create cloud tasks, upload
content to AI or submit homework. Installed-extension reload and live end-to-end
handoff remain unverified; background monitoring and production ingestion remain
unfinished. This local observation stage is not a substitute for the full PRD.

Reliability follow-up: removing a previously known deadline now creates one
deduplicated confirmation notice for the new deadline revision. Ordinary done,
reschedule and edit replies render the known transaction result without querying
Firestore after writes. Fifty-six command/scheduler/system tests and thirteen
Firestore-fake tests pass; the latter exercise the real prepared command actions
inside update-receipt transactions. Live production verification remains pending.

Homework observations now reuse the existing visible PDF-reference extractor and
retain only attachment source IDs and filenames. Nonempty results are explicitly
`observed_partial`, not complete or downloaded; empty results remain
`not_observed`. The local validator rejects URLs, extra fields, duplicate IDs and
inconsistent evidence states. Six Node extractor tests and seventeen Python
handoff tests pass. Non-PDF attachments, verified download state, cloud task
ingestion and live installed-extension testing are still unfinished.

Calendar runtime preparation — 2026-10-05: promoted the Phase 0 public-calendar
parser into `chronos.academic_calendar`; the CLI now imports that same code.
Both semester tables are read, invalid dated events fail visibly, and explicit
closure takes precedence over exam mode. Contradictory normal/closed evidence
requires confirmation. Six focused tests pass. A fresh public-source probe
parsed 103 events (23 closures, 2 normal-instruction, 4 exam-period and 3
confirmation-needed events), fingerprint
`e61d487557d0866522aa5aa44316fdea9935242e6e45ad56aacb2887fa88e4ec`.
Source: https://academic.ntou.edu.tw/p/405-1005-123146,c834.php?Lang=zh-tw .
Daily snapshot persistence, synchronization, notifications and production
suppression are still not connected. Do not call this calendar integration complete.

Calendar snapshot storage is now implemented on SQLite and Firestore. Snapshots
carry the exact official source URL, retrieval timestamp, HTML fingerprint and
validated events. Older responses cannot replace newer snapshots; next-day and
future-dated snapshots are not current. Six snapshot/policy tests pass. Network
synchronization, notification timing and production suppression remain pending.

## Owner-approved deferral — 2026-10-05

YiLi explicitly paused automatic lecture key-point generation and delivery.
This supersedes earlier instructions to keep implementing Phase 2 or comparing
models. Phase 2 is **not accepted or complete**. Do not resume generation,
lecture uploads, model comparisons, retries, deployment or automatic delivery
without a new owner instruction.

Preserve existing browser-session access, PDF download/persistence, material
selection, canonical notes, tests and diagnostic evidence. No data deletion,
paid-provider migration or replacement with combined summaries is authorized.

Reasons: repeated HTTP 503 on real lecture inputs, including text-only controls;
the latest draft failed before review. A read-only model metadata request
returned HTTP 200 and advertised generation support, which does not establish
generation availability. The official incident page failed to load, so a
provider-wide outage and the underlying cause remain unproven. Separately,
successful generations still contained unsupported inferences; availability
alone cannot satisfy the content-quality gate.

Restart requires explicit owner approval, a fixed page-scoped sample and a
bounded request budget. Verify both draft/review availability and source-grounded
content before enabling sequential generation/save/send. Complete the outstanding
Markdown-export acceptance before claiming full Phase 2 acceptance. Retain the
roughly three-page segmentation requirement; do not restart combined summaries.

Operational check at deferral: no matching Chronos automation configuration was
found in the local automation directory. Live Cloud Run configuration confirms
`CHRONOS_ENABLE_STUDY_TRACKING=false` and
`CHRONOS_ENABLE_INTERNAL_SCHEDULER=false`. Cloud Scheduler lists only the unrelated
`chronos-daily-tasks` job, which remains enabled. Authorized local process
inspection found no Python process matching the Study-worker command pattern.
No cloud configuration changes or generation requests were needed.

The following entries are historical evidence, not instructions to resume.

## Explicitly authorized stage probe result — 2026-10-05

YiLi explicitly authorized Lec0 physical pages 10–12 plus extracted text to
Google's Gemini 3.6 Flash free API, at most two requests, no retries, under the
previously accepted free-tier data policy. The previously denied probe was then
executed once using the existing `flash36stages` cohort and exclusive checkpoint.

Result: the **draft** request returned HTTP **503** after **2.62 seconds**.
Exactly **one** request was recorded; review did not start, no retry occurred,
and no candidate note was produced. Evidence is the private local
`.study-data/free-model-comparison-v10-flash36stages/results.json` together with
its attempt manifest. Production configuration, canonical notes and delivery
state were unchanged; no deployment occurred.

This localizes this attempt's failure to initial generation, not second-pass
review. It does not establish the underlying provider cause, general model
unavailability, or comparative content quality. Earlier small-text success
remains a separate observation. The specific authorization is now consumed as
a single no-retry test; do not reuse it for another upload or broader payload.
Phase 2 quality acceptance and owner Markdown-export acceptance remain open.

## Stage-specific probe prepared; external execution denied by tool review

The comparison adapter now records safe per-request stage (`draft` or `review`),
elapsed time and allowlisted outcome/status only. It never includes request
bodies, source text, provider error bodies, headers or credentials in telemetry.
Six focused mock-only tests passed (55 warnings, 0.30 seconds), including draft
versus review failure identification and no private content in observations.

Follow-up full local regression: **333 passed**, 525 warnings, 57.88 seconds.
This used mocked external services, not live lecture uploads. The new probe's
attempt directory remained absent; external execution is still awaiting the
specific authorization requested below.

The proposed `flash36stages` run was rejected before process creation by the
tool approval reviewer, citing insufficiently explicit authorization for the
exact document payload/destination. The attempt directory does not exist; no
new live request or result is claimed. Do not work around this denial. The user
was asked to explicitly authorize Lec0 physical pages 10–12 plus extracted text
to Google's Gemini 3.6 Flash free API, at most two requests, no retries, under
the previously accepted free-tier data policy. No production settings changed.

## Owner retains automatic delivery; alternative-model comparison — 2026-10-04

YiLi selected option 2: retain automatic segmented delivery and assess other
free-tier models. Review-before-publication requiring human approval is NOT
adopted. Earlier entries awaiting this product choice are superseded; content
quality and live export acceptance remain required.

Google's current official pricing page lists free standard input/output for
Gemini 2.5 Flash, 3.7 Flash and 3.6 Flash:
https://ai.google.dev/gemini-api/docs/pricing . This establishes published model
eligibility, not available quota or billing status for every project. Testing
uses the user's previously confirmed free-tier project and accepted data policy;
no billing settings, paid service tier, API key or production model were changed.

The fixed-input comparator uses Lec0 physical pages 10–12, the same v10 prompt,
original PDF plus extracted text, and the same bounded draft/review path. It
allows one attempt (at most two requests) per model, no automatic retries, and
exclusive attempt files prevent blind reruns. It writes only private local
candidates/results; never production notes or Telegram messages.

Initial observations: `gemini-2.5-flash` returned HTTP 404; `gemini-3.7-flash`
returned HTTP 503. Neither yielded a draft, so neither has a quality score.
A separate read-only models-list request returned HTTP 200 and listed both
models, plus 3.6 Flash, 3.8 Flash, 3 Flash Preview and 3.1 Flash Lite, with no
remaining page. Listing therefore does not prove generation availability; the
2.5 result must not be described as a confirmed global retirement.

3.6 Flash's same-input comparison returned 503 after 54.45 seconds. A separate
tiny text-only control returned HTTP 200 with a candidate, establishing that
this credential/model combination can respond to at least that request. Two
bounded document-content controls still returned 503: extracted text without
the binary PDF (15.56 seconds), and that same text in JSON mode with the schema
contract moved into the prompt (6.61 seconds). The latter retains the same local
Pydantic/source checks; it is diagnostic only, not a production fallback.

These samples do not establish a unique root cause: neither PDF attachment nor
native schema enforcement alone explains every observed failure, and transient
capacity/request-complexity effects remain possible. No alternative produced a
complete reviewed note, so there is no comparative content-quality winner.
All cohorts have exclusive attempt guards; no automatic repeats or billing
changes occurred. Further same-batch probes stop here. Recheck provider readiness
before another document-quality run, rather than silently selecting an untested
model or adopting human approval against the owner's decision.

Final local suite: **331 passed**, 507 warnings, 26.77 seconds. The comparison
tests cover repeat-run exclusion, safe failure classification, unchanged model
defaults, prompt-version guards and the two isolated request-format controls.
The configured study model remains `gemini-3.1-flash-lite`; no deployment occurred.

## Page-text assisted review and publication decision — 2026-10-04

The local v10 pipeline extracts each selected 1–4-page slice once in the isolated
parser. It passes page-scoped text alongside the original PDF to generation and
review, then reuses that text for local quotation/weekday checks. Each text page
includes source ID, local page and original physical page; it is explicitly
untrusted data, not a system instruction. Text count and the existing 100,000
character budget are checked before provider requests. No additional model call
was added: the limit remains two requests per attempt. Full local regression:
**327 passed**, 471 warnings, 29.07 seconds.

The bounded v10 Lec0 run completed five segments once each, saved only under
`.study-data/lec0-lite-quality-v10/`. The office/laboratory association and neutral
`Others` list errors from v9 were corrected; the group-work exception remained
present. However, source comparison still rejects full semantic acceptance:

- Pages 10–12 again infer xv6 skills under possible exam topics from the mere
  existence of an xv6 assignment, despite the two-pass review policy.
- The same segment expands `10% penalty per day late` into a deduction based on
  the assignment's total score; the source does not specify that denominator.

No canonical production note was replaced, no revised note delivered, and no
deployment performed. Testing completion does not imply content acceptance.
Repeated same-model prompt changes have not established reliable automatic
publication. Further blind prompt-variant runs are stopped pending a product
choice: retain free-tier generation with explicit review-before-publication, or
retain the automatic/free-tier requirements and assess other eligible models.
The user has been asked; neither scope change has been assumed or implemented.
The existing PRD automatic delivery contract remains in force until a decision.
The separate owner `/export` acceptance also remains outstanding.

## Bounded source-review experiment — 2026-10-04

v8 made five single-attempt local requests. Four drafts passed structural/source
checks; pages 7–9 failed validation or saving. That version did not retain the
candidate or a stage code, so the exact 7–9 failure is unknown and must not be
attributed specifically to the quotation check. Pages 10–12 still inferred xv6
exam content from an assignment-type quote; pages 13–14 omitted the group-work
exception. This confirmed that quotation provenance alone is insufficient.

The local v9 adapter now makes at most two requests per attempt: draft generation
and a distinct source-review request containing the same original PDF segment,
metadata and untrusted draft. Only the reviewed output reaches the pipeline.
Review failure never falls back to publishing the initial draft. Existing job
retry limits still apply to the entire attempt; there is no correction loop or
paid-model fallback. The second call increases provider usage and latency; it
is the same model, not an independent ground-truth authority. PRD records this
mechanism and its limits. Diagnostics now retain private candidate JSON and an
allowlisted processing-stage label, without provider response/error dumps.

The v9 experiment completed all five segments once each (ten model requests),
saved locally under `.study-data/lec0-lite-quality-v9/`. None was delivered or
saved as a production canonical replacement. Visual inspection of source pages
2, 3, 7, 8, 10, 11, 12 and 13 found:

- Pages 4–6 preserve specific learning-objective quotations and appropriately
  tentative predictions. Pages 7–9 no longer invent a date conflict.
- Pages 10–12 no longer predict xv6 exam content from grading/lab existence and
  preserve attendance-without-penalty and encouraged-participation semantics.
- Pages 13–14 preserve the explicitly permitted group-work exception.
- Quality acceptance still fails: pages 1–3 associate ECG 703 with IDA Lab,
  although the source labels it as the instructor's office; pages 10–12 treat
  the separate neutral `Others` list as excluded topics. Missing-information
  wording also remains broader than the supplied segment in some bullets.

Therefore a second model pass improved these samples but did not establish
reliable factual correctness. Do not deploy this as a completed quality fix or
silently relax the evidence-grounded requirement. Original notes remain intact.
Live owner Markdown-export acceptance is still a separate, unverified gate.

Final local regression run: **323 passed**, 435 warnings, 100.49 seconds.
The two integration assertions were updated to account for exactly two requests
per successful segment, while a first-request 503 still costs only one request.
Review failures (invalid output, 429, 503) return no initial draft and perform no
inline retry. A fresh read-only production query returned zero owner export
receipts. No deployment occurred; revision `chronos-00018-c68` was not changed
by this work.

## Inference provenance gate, not semantic acceptance — 2026-10-04

The v7 local experiment saved all five Lec0 segments, once each, with semantic
review pending. Inspection of the 7–9 and 10–12 drafts still found unsupported
reasoning: a speculative administrative conflict between December 24 and 25,
and a prediction of xv6 exam content based only on the existence of a lab.
Successful generation and structural validation therefore did not pass quality
acceptance. These local drafts did not replace canonical notes or get delivered.

The local v8 contract now requests original-language source excerpts for each
exam inference. Before segmented persistence, the existing isolated PDF parser
extracts the supplied pages, then verifies every excerpt against its exact cited
source/page. Unicode presentation normalization and whitespace differences are
tolerated; missing excerpts, unextractable text and invented quotations fail
closed through the existing uncertain state, without automatic regeneration.
Excerpt page numbers are remapped alongside citations and rendered in the note.
Historical drafts remain readable, but cannot bypass the new generation gate.

This establishes excerpt provenance only, NOT logical support or factual
correctness. A regression explicitly demonstrates that a real grading-weight
quote can pass matching while failing to justify a technical exam prediction.
General claims and uncertainty prose still need semantic verification. Scanned
PDFs without extractable text cannot pass this inference check. No v8 live
request, deployment, canonical replacement or Telegram delivery has occurred.

Validation: the full local suite passed 318 tests (390 warnings, 23.97 seconds).
A subsequently added positive end-to-end test also passed (one test, 10 warnings,
7.44 seconds). It uses a genuine text-bearing synthetic PDF through isolated
slicing and extraction, verifies excerpt and citation remapping to physical page
4, canonical Markdown export equality, mocked delivery and idempotent replay.
Negative pipeline cases verify missing/invented excerpts produce no saved note,
no delivery and no automatic second generation. These are local checks, not
production Telegram or Firestore acceptance.

Next acceptance work: review a bounded live output against its source. Do not
classify Phase 2 as complete on quote matching alone.

## Owner-approved first-release catalog scope — 2026-10-04

YiLi explicitly accepted an observed-materials first release after the coverage
limitation was explained. Complete enumeration and genuine upload-time sorting
are deferred, not falsely reported as implemented. PRD sections 3.3 and Phase 2
now reflect this decision. Existing partial-catalog/unknown-date labels remain
required. Content quality and live Markdown export acceptance are unchanged;
Phase 2 remains incomplete. Earlier entries describing complete enumeration as
a first-release gate are historical and superseded only on this scope point.

## Source-check integration regression — 2026-10-04

The complete local suite passed **309 tests** (372 warnings, 86.85 seconds).
The pipeline rejection test now covers both a miscategorized exam date and an
invented weekday absent from a real parsed synthetic PDF. Both produce no note,
no Telegram call and no automatic second generation on replay. These changes
remain local; deployed revision `chronos-00018-c68` is unchanged. Passing this
suite does not establish corrected live output or complete course metadata.

## Recipient correction notice — 2026-10-04

Sent one clearly labelled correction notice to the configured Chronos owner chat
for the five already delivered v4 acceptance notes. A stable delivery-ledger key
returned `sent`, preventing blind repeat notices. It explains the false date
overlap, fact-versus-inference classification, physical-page-14 reference and
segment-only absence scope, and warns that the notes have not passed quality
acceptance. Original notes/messages were preserved; no replacement generation
or deletion occurred. This notification is not a completed semantic fix.

## Offline replay of actual v6 failures — 2026-10-04

Replayed the saved v6 drafts against the hash-verified local PDF, without new
model requests or external writes. The 7–9 draft was rejected by the new
weekday-presence check: its invented weekday was absent from the cited segment.
The 10–12 draft was rejected by the inference-confidence check, including its
overstated rationale. These are real negative samples, not only synthetic tests.

The weekday check extracts at most four pages in the existing isolated parser
and matches supported Chinese/English weekday names on cited pages (or the
supplied segment for uncited uncertainty prose). Presence is not entailment:
it does not prove a weekday is associated with a particular date, recognize
every spelling, or correctly read scanned PDFs. The standalone quality probe
now applies the same checks before remapping citations and saving a draft.
These guards block the observed failures but do not produce replacement notes
or establish broader semantic accuracy. No deployment occurred.

## Prompt v6 classification experiment — 2026-10-04

Two local-only requests for pages 7–9 and 10–12 succeeded once each. Explicit
examples moved exam dates into the factual concepts section, and both outputs
passed the narrow lexical category gate. The drafts remain under
`.study-data/lec0-lite-quality-v6/`; no canonical replacement or delivery occurred.

Semantic acceptance still failed. The 7–9 output invented a weekday assertion
for December 25 that the supplied segment does not establish. The 10–12 output
claimed xv6 assessment content was highly likely and would directly affect
evaluation based only on assignments and grading weights, overstating its
evidence. This is direct evidence that passing the known-category gate does not
imply factual grounding. Do not promote v6 as accepted or continue unbounded
prompt variants until one happens to look correct. A broader quality strategy
must explicitly handle unsupported added detail and inference confidence.

## Quality-change regression checkpoint — 2026-10-04

The full local suite passed **301 tests** (363 warnings, 32.90 seconds) after
the v5 physical-page context, known-category rejection and application-owned
uncertainty scope changes. The latter explicitly limits missing-information
claims to the supplied segment; it does not repair unsupported model prose.
No new deployment or canonical-note replacement occurred. Cloud Run remains
on the earlier v4 deployment; these local quality changes are not a claim that
the failed live semantic acceptance has passed.

## Known-error gate integration — 2026-10-04

Rendering now rejects recognizable administrative date/schedule/grade-weight
claims in `exam_inferences`. This is a conservative lexical tripwire, not a
semantic classifier; paraphrases can evade it and mixed legitimate predictions
can require review. It does not silently move claims into a different category.

A synthetic pipeline regression injected the observed final-exam-date category
error. It verified `uncertain`, zero canonical notes, zero Telegram sends, and no
second model call when replayed. Six focused checks passed. The existing
`uncertain` state currently covers both unknown provider outcomes and invalid
generated content; neither authorizes blind retry. This does not repair the
already delivered acceptance notes or establish that v5 outputs meet quality
requirements. These changes have not been deployed.

## Prompt v5 targeted live review — 2026-10-04

Four local-only Flash Lite requests (pages 1–3, 7–9, 10–12, 13–14) each
succeeded once, saved under `.study-data/lec0-lite-quality-v5/`. No Firestore
notes were overwritten and no Telegram delivery was made. Original physical
page metadata was supplied; the former prose “page 2” error did not recur.
The model correctly stopped calling December 24/25 overlapping dates.

The quality gate still fails: both 7–9 and 10–12 put explicit schedule facts
inside `exam_inferences`, despite the v5 instruction. The final segment still
uses unscoped absence wording about an email address available elsewhere in
the document. Prompt instructions alone are not reliable enforcement of output
categories. Four successful HTTP results are not four semantically passing
notes. Preserve these drafts as negative regression examples; do not promote
v5 as a verified quality fix or automatically replay completed selections.

## Generated-content review failed — 2026-10-04

Read back all five canonical acceptance notes and compared their claims with
the local source text, especially physical pages 7–14. Delivery success must not
be treated as semantic acceptance. Confirmed issues in these actual outputs:

- Segment 7–9 calls December 24 and December 25 overlapping times. These are
  different dates; the source explicitly lists December 25 as Constitution Day
  and December 24 as the final exam. The invented overlap is incorrect.
- The same segment places explicitly stated exam dates under inferred exam
  topics, confusing source facts with predictions.
- Segment 1–3 says the document lacks syllabus/grading/textbook information;
  these exist on later pages. Absence must be scoped to the supplied segment.
- Segment 13–14 refers to Q&A on “page 2” in free-text uncertainty, while the
  physical source is page 14. Structured citations are remapped, but arbitrary
  prose page references currently are not.
- Segment 10–12 speculates that repeated Security/Protection labels imply
  inconsistency without establishing one; this is not a justified uncertainty.

No canonical note was overwritten or regenerated by this review. The next
quality change must distinguish facts/inferences, scope absence claims to the
input range, and supply physical page identity before generation rather than
assuming postprocessing structured citations fixes all prose. Text inspection
establishes the date/page errors; a complete visual layout audit is still pending.

## Owner-confirmed Firestore workflow — 2026-10-04

The real owner replied to the labelled acceptance prompt through the deployed
Telegram webhook; shared Firestore transitioned the session to `answered`.
After refreshing the local five-activity catalog, one selection prompt was sent.
Readback first showed Lec0 selected but unconfirmed; no generation ran until the
owner pressed the explicit completion button and Firestore showed confirmed.

One local worker pass using default credentials and the explicit production
Firestore route returned `processed: 1`, `statuses: {sent: 1}`. A new repository
read found five canonical Flash Lite notes with page ranges 1–3, 4–6, 7–9,
10–12 and 13–14, under the clearly labelled nonofficial acceptance course.
A subsequent worker pass returned `processed: 0`, with no new work scheduled.
This verifies the owner callback, local PDF resolution, real provider generation,
Firestore persistence and Telegram delivery path. It does not establish semantic
accuracy of all five generated segments, live Markdown export, complete course
enumeration or upload-time sorting. Recurring Study reminders remain disabled.

## Authorized Cloud Run deployment — 2026-10-04

Deployed feature commit `724d261` after source-upload protection commit `44490b0`.
The deployment inventory contained 86 files and excluded local Study data,
environment files, virtual environments and test databases. The deployment used
the existing service and secret bindings, not the broad provisioning script.
The full local suite passed 293 tests before deployment.

Cloud Run revision `chronos-00018-c68` became Ready and received 100% traffic;
an independent GET `/health` returned `ok`. Readback confirmed Firestore backend,
Study model `gemini-3.1-flash-lite`, and both `CHRONOS_ENABLE_STUDY_TRACKING` and
`CHRONOS_ENABLE_INTERNAL_SCHEDULER` set to false. Existing external daily-task
scheduling was not changed. No continuous local summary worker was started.
Health and configuration checks do not prove a real owner reply/selection or
end-to-end note delivery; those remain separate acceptance checks.

## Default-credential persistence verification — 2026-10-04

Executed `python -m scripts.verify_phase2_firestore --default-credentials`.
All eight checks returned true using the worker's default-credentials path:
note roundtrip, first-result preservation, selection roundtrip, completed job,
segment metadata, retry deadline, immutable execution binding, and cleanup.
The verifier retained the exact-project guard and random isolated collection
prefix. Its three synthetic documents were deleted and their absence verified;
no production record was removed. This closes the worker authentication and
tested persistence-permission gap. Actual owner interaction, complete catalog
metadata and deployed end-to-end workflow remain unverified.

## Worker default-credential read recovered — 2026-10-04

After the operator reauthenticated, the worker's unmodified default-credentials
path successfully read the material-selection collection in `yili-chronos-prod`,
database `(default)`, prefix `chronos`, with a 15-second request timeout.
The bounded query returned no selection. Safe output was
`default_credentials_read: true`, `selection_present: false`, `writes: false`.
This supersedes the earlier missing-credentials and permission-denied read
blockers, but does not prove write permission, an owner-confirmed selection,
production deployment, or end-to-end delivery. No worker was started and no
message, model request or database write was performed by this check.

## Worker authentication blocker — 2026-10-04

A single read-only check instantiated the real Firestore repository with the
worker's default-credentials path and attempted a bounded selection read.
It returned `DefaultCredentialsError`, before any write or Telegram/provider
operation. The explicit gcloud-token verifiers therefore do not establish
worker authentication readiness. No token was printed or persisted by this check.

The operator must configure local Application Default Credentials through
`gcloud auth application-default login` (interactive Google consent), or supply
another explicitly approved runtime identity. Do not silently copy a short-lived
CLI access token into configuration, export service-account keys, or launch a
worker with an unverified identity. After interactive authentication, repeat the
bounded read against the intended project/database/prefix before activation.
This is a user identity/consent boundary, not a Gemini or PDF failure.

## Explicit shared worker route — 2026-10-04

The launcher accepts `--firestore-project`, `--firestore-database` and
`--firestore-prefix` together as a process-local override. Partial routes are
rejected, and the original settings object and `.env` are unchanged. Eleven
launcher tests passed. A safe configuration-only check for the observed route is:

```powershell
.venv/Scripts/python.exe -m chronos.run_summary_companion --check --firestore-project yili-chronos-prod --firestore-database '(default)' --firestore-prefix chronos --course-map '{"operating-systems":"192072"}' --free-tier-confirmed
```

This is not an activation command. It performs no network call, verifies no
Application Default Credentials, and starts no background process. The prior
live Firestore verifiers used an explicitly obtained in-memory gcloud token;
their success must not be confused with the launcher's default-credentials
readiness. Actual owner progress/selection and production deployment remain
separate acceptance gates.

## Shared runtime routing audit — 2026-10-04

Read-only Cloud Run inspection confirmed `chronos` in `asia-east1` routes to
Firestore project `yili-chronos-prod`, database `(default)`, prefix `chronos`.
No explicit `CHRONOS_ENABLE_STUDY_TRACKING` environment entry was returned;
the current repository default is false. This does not independently establish
the source version in the deployed container.

A read-only Firestore collection-name listing found only `chronos_meta` and
`chronos_telegram_updates` in that namespace. It found no populated Study
session, selection or canonical-note collections at that instant. Combined with
the local SQLite preflight, this establishes that the current local worker is
not configured to consume the deployed webhook's Firestore state. No real
answered session or confirmed PDF selection was demonstrated in that shared
namespace, so an automatic generation pass cannot serve as live acceptance.

Activation requires deliberate shared routing and a real owner progress/selection
interaction (or a clearly isolated synthetic workflow, reported as such).
Do not invent class progress, copy local test selections into production, or
enable recurring notifications merely to make an acceptance check pass.
This audit changed no service, environment file, scheduler or database record.

## Worker preflight — 2026-10-04

`python -m chronos.run_summary_companion --check` now reports allow-listed
configuration booleans without initializing databases, calling providers or
messaging Telegram. `--check` takes precedence even when `--enable` is supplied.
Seven launcher tests passed, including missing-database noncreation and secret
omission. Exit zero means the inspection executed, not that deployment is ready;
`remote_readiness` remains `not_checked`.

The real local check found SQLite selected but its configured file absent,
no explicit Firestore project, and both Telegram/provider credentials present.
The Lite Study default, local PDF directory and catalog file were present.
No course map or free-tier flag was supplied for this diagnostic invocation;
that does not revoke the user's earlier free-tier approval. No configuration
was changed and no worker was started. Shared production Firestore routing must
be deliberately configured and verified before claiming the live integration.

## Follow-up detail and Firestore checks — 2026-10-04

Lecture 0's live detail page exposed an activity time of “未設定”, the PDF name,
size and reference-download control, but no upload timestamp. Activity scheduling
is not file upload time. This sample therefore does not close the latest-first
catalog gate; other materials were not extrapolated from it.

The isolated Firestore verifier now also binds model, prompt and pagination
versions, reopens the repository, accepts the same binding and rejects a changed
model while preserving the original stored binding. The real service run returned
all eight evidence flags true, including `execution_binding_roundtrip` and
`cleanup_complete`. Only the three synthetic documents in the fresh verification
namespace were removed. No real notes, selections or lecture contents were
uploaded or deleted. This establishes the binding's real-backend behavior,
not production worker activation or Telegram-to-Firestore end-to-end completion.

## Live browser catalog boundary — 2026-10-04

The connected Chrome tab for course 192072 became readable again through the
supported browser connection. The material page showed five activity attachment
controls (1017741, 1017744, 1018659, 1022967, 1028844), matching the previously
observed catalog. Read-only DOM inspection found no next/previous/load-more
labels and no `time` elements. Absence of those controls alone is not proof of
complete enumeration or of the absence of unpublished/inaccessible materials.

Inspection of the page's actual sort-control markup exposed only chapter and
title predicates, with ascending/descending direction. No upload-date sort was
present. Accordingly, `uploaded_at` must remain unknown; neither activity IDs,
filename order nor local observation time may substitute for upload time.
The latest-first requirement is still unverified, not silently satisfied by
chapter order. This inspection used no cookie, token, application-state or
network interception, and did not download or modify any course material.

## Verification audit — 2026-10-04

The current full local Python suite passed: **288 passed, 336 warnings in
21.29 seconds**, using `pytest -q -p no:cacheprovider --basetemp
.pytest-segments-check --disable-warnings -o faulthandler_timeout=45`.
The focused workflow checkpoint and two integration scenarios also passed
(3 tests). A prior restricted-environment test process stalled and was stopped;
only the completed runs are counted. These tests do not establish production
deployment or live browser catalog completeness.

Phase 1 interview-document attachment delivery was recovered from the actual
historical tool result at 2026-10-02 15:16:27 UTC: the authorized Phase 1 sender
returned `document_delivery=confirmed`. Its sender checks Telegram success and
a returned document object, rather than treating a text link as file delivery.
The document was committed in `561cfc6`; this audit does not claim a new send or
that the recipient read it. No duplicate attachment was sent during this audit.

## Live Lite sequential workflow — 2026-10-04

Executed `scripts/verify_lec0_segments.py --execute-authorized-lite-first-six`
with `gemini-3.1-flash-lite` and prompt `study-segment-v4`. Both Lec0 ranges
(1–3, then 4–6) returned `sent`; two canonical notes were saved in the isolated
SQLite test database. Telegram messages were explicitly labelled test material.
Replaying both ranges with provider and Telegram methods replaced by forbidden
network-operation guards returned `sent` twice without invoking either method.
The safe receipt reports `duplicate_suppressed: true`, `saved_notes: 2`, and
`production_database_written: false`, under the ignored directory
`.study-data/lec0-lite-first-six-workflow-v4/result.json`.

This verifies live generation, local persistence, sequential Telegram delivery
and completed-job replay suppression. It does not verify Firestore-backed
production delivery, complete course enumeration, or semantic correctness of
these newly generated outputs. Earlier local quality reviews apply to their
specific drafts, not automatically to this run.

## Immutable execution configuration

Confirmed selections now atomically bind model, prompt and pagination versions
before generation. Resuming with a different configuration returns context_mismatch
instead of creating a fresh fingerprint and silently repeating generation/delivery.
Historical selections that already attempted processing without a binding require
review; their budgets are not reset by a software upgrade. This protection is in
the regular local companion path as well as the isolated verification checkpoint.

## Live Firestore segment/retry regression — 2026-10-04

The guarded `scripts.verify_phase2_firestore` verifier ran against the existing
Chronos project, using only three synthetic documents in a fresh random prefix.
All checks passed: note_roundtrip, first_result_preserved, selection_roundtrip,
job_completed, segment_metadata_roundtrip, retry_roundtrip and cleanup_complete.
A new database client read the original page range, segment ordinal/total and
pagination version. The persisted retry deadline blocked an early claim and
allowed a second attempt at the deadline. Synthetic clock advancement tests state
logic, not elapsed production scheduling. Only the three created documents were
deleted and absence verified. No actual PDF/note text was uploaded or messaged.
This confirms the real Firestore adapter, not production worker deployment.

## Study model selection — 2026-10-04

Flash Lite prompt-v4 pages 4–6 succeeded on one request. Comparison with previously
inspected rendered source pages supports the OS domains, xv6 approach, system-call
boundary, concurrency, address translation and storage statements. The possible
exam point is labelled inference with a source rationale; no exam format is claimed.
Together with the v4 first-segment correction, these two samples support selecting
Flash Lite for further Study integration, not a long-term reliability claim.

The local Study launcher now uses `CHRONOS_STUDY_GEMINI_MODEL` (default
`gemini-3.1-flash-lite`), independent of the general `CHRONOS_GEMINI_MODEL` setting.
Other Chronos AI features are unchanged. No running worker was started or restarted,
and neither v4 draft was sent to Telegram. Real Firestore-backed ordered delivery
of these reviewed segments and complete course catalogs remain outstanding.

## Flash Lite targeted quality correction — 2026-10-04

Prompt v4 adds a general interval-overlap rule and distinguishes genuine source
contradictions from speculative doubts. A single Flash Lite request for pages 1–3
succeeded and was saved only locally in `.study-data/lec0-lite-quality-v4/`.
The output preserved 09:20–12:05 and 12:00–13:00, correctly called them overlapping,
made no exam prediction and did not question the semester label. This passes the
targeted regression case, not a broad semantic-accuracy benchmark. The previous
v3 drafts remain untouched. Sixteen focused adapter/integration tests passed.
No default model was changed and no Telegram message was sent.

## Flash Lite first-six-page review — 2026-10-04

`gemini-3.1-flash-lite` with prompt v3 generated both page ranges (1–3 and 4–6)
on their first respective attempts. Draft JSON/Markdown and safe receipts remain
under `.study-data/lec0-local-v3-31-lite/`; neither draft was sent to Telegram.
This is two successes, not a reliability benchmark or production acceptance.

Visual source comparison used rendered PDF pages 4–6 (source-04.png through
source-06.png in the same ignored directory). Findings:

| Claim | Source evidence | Review |
| --- | --- | --- |
| Four OS domains | Page 4 lists foundations, concurrency, memory, storage/I/O | Supported |
| Question, decomposition, xv6 implementation | Page 5 explicitly gives all three stages | Supported |
| System-call boundary, concurrency, address translation and file blocks | Page 6 learning objectives | Supported |
| Synthesis traces a request to hardware | Page 6 also specifies the return path | Supported but compressed |
| Possible concurrency/memory exam topics | Page 6 states learning objectives, not an exam plan | Only acceptable as explicitly labelled inference, never a promise |
| No precise domain teaching order | Page 4's A–D list gives organization, not a dated schedule | No schedule inferred |

Pages 1–3 still fail a targeted quality check: the draft calls a five-minute
overlap between class and office hours a continuous handoff, instead of identifying
the overlap. Therefore do not promote the first draft or claim the whole six-page
output passes semantic review. No new generation, production setting changes or
Telegram sends were performed during review. Original drafts are preserved.

## Gemini 3.7 comparison — 2026-10-04

Explicitly authorized first-segment trial with `gemini-3.7-flash` and prompt v3
returned valid structured content on one attempt. It correctly avoided an exam
prediction and identified the five-minute schedule overlap. It also introduced
an unsupported suspicion about the consistent Fall 2026 / 1151 semester labels;
semantic acceptance is therefore not complete.

After authorization to make subsequent engineering decisions without repeated
questions, one independent trial of pages 4–6 with the same model returned 503.
No additional retry was issued. Both trials stayed local, with separate exclusive
attempt checkpoints; no Telegram delivery or default-model change occurred.
One success followed by one 503 is insufficient evidence to promote 3.7 as a
reliability fix. Preserve the existing default until stronger evidence exists.

## Authorized local-only v3 test — 2026-10-04

User explicitly authorized a new first-six-page test, local storage only, with
three attempts per segment. `scripts/verify_lec0_local_v3.py` completed all six
bounded attempts; every response was HTTP 503. No generated drafts were saved,
no Telegram requests were made, and no old notes/jobs were replaced. Safe attempt
results remain in `.study-data/lec0-local-v3/results.json` (ignored). Both segment
budgets are exhausted; do not restart under a different directory to evade them.
Prompt v3 semantic quality remains unverified because no content was returned.

## First-segment semantic review — 2026-10-04

Read back the persisted test note and compared it with source text and rendered
physical pages 2–3. Core administrative values and page references matched. The
source contains a five-minute overlap between class end and office-hour start;
the note preserved the values but did not flag the inconsistency. Its suggestion
that the administrative material would not appear in exams is not established by
the slides, even though labelled inference. This output is therefore not a full
semantic acceptance pass. Source images also avoided relying on garbled Chinese
text extraction. Poppler reported missing fallback fonts, but inspected pages
were readable for this comparison; no PDF layout-quality claim is made.

Prompt `study-segment-v3` now explicitly prohibits unsupported negative exam
predictions and asks for source contradictions to be flagged. Relationship lists
may be empty to avoid forcing invented connections. These are preventive changes,
not proof the model will comply. The already delivered test note is preserved as
historical evidence, not silently replaced. No additional generation or delivery
was performed for this review.

## Authorized Lec0 first-six-page workflow — 2026-10-04

Ran `scripts/verify_lec0_segments.py` with explicit user approval for a workflow
test, not official class notes. Canonical records and job/delivery receipts are
isolated in `.study-data/lec0-first-six-workflow-v1/notes.sqlite3`; production
storage and TronClass were not modified. Telegram messages have a test banner.

- Pages 1–3: two explicit 503 failures, then generation/persistence and Telegram
  API-confirmed delivery on attempt three. Later passes skipped this completed
  segment instead of generating or sending it again.
- Pages 4–6: three explicit 503 failures; durable job reached `failed` and stopped.
  No successful second-segment note or delivery is claimed.
- Early polling did not increment the attempt count. Each segment had its own
  bounded retry ledger. Six total provider attempts, including one success.
- Two-segment end-to-end acceptance and semantic review remain incomplete.
  The verifier's final full-success duplicate guard was not reached; completed
  first-segment reuse was observed during second-segment recovery attempts.

This is real partial-delivery and retry-exhaustion evidence, not Phase 2 completion.

## Controlled comparison and 503 recovery — 2026-10-04

One authorized request per case returned HTTP 200: text (6.54s), text with
current schema (4.62s), and the first three physical PDF pages with schema
(7.36s). Both schema cases passed local structure/citation-range validation.
No generated content was retained, no canonical note was created, and no
Telegram message was sent. This supports transient service failure rather than
an invariably invalid request, but does not establish semantic accuracy or
long-term availability. Safe results are in the ignored local directory
`.study-data/gemini-controlled-comparison-v1/results.json`.

Explicit HTTP 503 now uses the existing durable generation-job ledger: at most
three total attempts, with 60–75s then 120–135s backoff. The watch worker retries
on a later pass, not by sleeping or retrying inside the HTTP adapter. Timeouts,
transport failures, other ambiguous errors and invalid outputs remain uncertain.
Previously uncertain jobs are not automatically unlocked. Telegram delivery
deduplication is unchanged. This is offline-tested recovery, not deployed or
live retry evidence.

The vertical-slice regression now also uses a six-page PDF and an initial mocked
503. It proves authenticated selection confirmation, no provider call before the
persisted retry deadline, two independent three-page requests after recovery,
two canonical notes, original-page citation remapping, duplicate-pass suppression,
and Markdown export. `tests/test_phase2_integration.py`: two scenarios passed.
All external generation and Telegram delivery remain mocked in this test.

## Scope replacement — 2026-10-04

This section supersedes historical combined-summary requirements below.
The PRD now requires independent per-PDF key-point extraction, normally three
physical pages at a time, followed by automatic ordered delivery. No combined
summary development continues. The production companion now calls the segment
orchestrator; the legacy pipeline entry remains for compatibility tests only.

- `physical-three-v1`: deterministic complete coverage, final short segment allowed.
  Explicit boundary hints support two/four pages; automatic semantic boundary
  detection is not implemented or claimed. Current companion uses fixed triples.
- Each model request receives one actual sliced PDF of at most four pages.
  Parser subprocess has a time limit and does not inherit provider secrets.
  Model citations are checked against the slice and mapped to original pages.
- Every segment stores original source hash, page range, ordinal/total and policy
  version. Fingerprints include range and policy; canonical Markdown is saved
  before delivery. Existing job/receipt ledgers handle resume and deduplication.
- Processing stops on rejection or uncertain outcome before starting later segments.
  Explicit rejection follows bounded retries; ambiguous outcomes need review.
- No minimum character quota. File order is stable source-ID order; each file's
  segments are ascending pages. Telegram may further split long segment messages.

Verification: 32 focused offline tests passed, including exhaustive partition
coverage for 1–1000 pages, actual slice parsing, repeat-run deduplication and
second-segment rejection/resume. These are not live provider or Telegram receipts.
Outstanding: live small-segment generation, citation semantic review, real ordered
Telegram delivery, and production Firestore/worker integration. Earlier Gemini
503 observations remain historical. A one-shot real request using only the first
three pages returned `outcome_uncertain`; no note was created or sent. Evidence:
ignored local `.study-data/live-segment-probe-v2/result.json`. The exclusive attempt
checkpoint prevents automatic repetition. This run cannot distinguish timeout,
server error or invalid output; subsequent diagnostics now preserve only an
allowlisted category and HTTP status, without provider bodies or credentials.
Multi-file isolation and resume tests also pass. Sisyphus update delivery timed
out on the previous turn and remains unknown; it has not been blindly resent.

## Acceptance audit — 2026-10-03

### Current follow-up evidence (2026-10-04)

Read-only network checks returned HTTP 404 from the Gemini API root and 302 from
the Telegram API root: both hosts were reachable, not proof of generation or
delivery. Authenticated configured-model metadata returned HTTP 200 and advertised
`generateContent`. Thus missing model / wholly unavailable host is not supported
by current evidence; the previous generation outcome remains unknown.

Each Telegram message for a segment now repeats filename, original physical-page
range, segment ordinal and message ordinal, including overflow messages. `/notes`
also distinguishes segments. Segment delivery has its own versioned receipt keys;
legacy note delivery keys are unchanged. Seventeen focused tests passed including
multi-message labels, UTF-16 message bounds, repeat suppression and multi-PDF
separation. No real lecture message was sent during these checks.

**Not accepted.** Component implementation and local integration do not prove the
requested live workflow. The chronological checkpoints below are historical;
this requirement-level audit takes precedence when interpreting completion.

### Native download implementation — pending live verification

Second-material checkpoint (2026-10-03): following the user's Lec1 download,
independent indexed readback verified Lec1 at 2874011 bytes and 81 parsed pages.
Lec0 remained readable at 466300 bytes and 14 pages. Both passed indexed hash
verification, had distinct content hashes, and totaled 3340311 bytes. This proves
two separate locally persisted inputs, not semantic quality or live multi-PDF
generation. No Gemini request was made for this verification.

Repeated-transfer checkpoint (2026-10-03): after the user's repeat action, local
inspection found three distinct randomly named native download files in the
configured Chronos download directory. All three were 466300 bytes and matched
the canonical indexed SHA-256. The canonical content-addressed store contained
one blob for this identity; indexed readback succeeded and parsed as 14 pages.
The index's latest saved_at was 2026-10-03T07:28:42.283818+00:00.
This verifies short-interval repeated-download content consistency for this one
PDF/session, not long-term reliability, session renewal or other attachments.
Native download originals remain on disk intentionally; canonical deduplication
does not mean Chrome's download directory contains no duplicates.

Live checkpoint (2026-10-03): user popup reported `persisted` for the first OS
lecture PDF. Independent local readback through `PdfStore.read` verified the
stored bytes against the indexed SHA-256 and size: 466300 bytes. The isolated
PDF parser returned 14 pages. This establishes one successful native-browser to
local-storage transfer, not repeated-download stability, semantic correctness,
complete-course coverage or Gemini generation. A second independent transfer of
the same source and comparison with this stored identity remains the next gate.

The user approved the Chrome `downloads` permission after confirming that the
native site download produces an openable PDF. The observed redirect destination
hostname was `tcmedia.ntou.edu.tw`; no bearer path is retained here.
The popup now uses Chrome's native download API after the content fetch reports
a redirect, querying only its returned download ID. It checks extension ownership,
completion, danger status, final HTTPS hostname, size and generated filename before
requesting local import. Final-host validation is after Chrome's network transfer;
it does not filter every redirect hop. Chrome handles authentication and redirects.

The receiver's optional `--native-download-root` confines import to one directory
and random 32-hex PDF basenames, rejects traversal and symlink escape, rechecks byte
count and PDF envelope, and uses the existing content-addressed atomic storage.
It never deletes the original download. The local-process trust limitation remains;
this is not protection against a malicious process racing filesystem changes.

29 JavaScript tests and 12 focused Python tests passed. Live native fallback and
import remain unverified. Keep the popup open; closing it can interrupt monitoring,
not Chrome's download. The current receiver uses
`C:\Users\User\Downloads\Chronos`; a customized Chrome download directory needs
matching configuration. No Gemini request is part of this test.

| Requirement | Current evidence | Remaining gate |
| --- | --- | --- |
| Course PDF list, newest upload first | Activity DOM extractor emits uploaded_at=null; snapshots explicitly incomplete | Observe and implement complete course traversal and genuine upload metadata; do not substitute observation/save time |
| Browser-session PDF retrieval | Bounded content-script download, popup handoff, local blob/index tests | Reload installed extension, configure its exact ID, verify real popup transfer; automatic retrieval of Telegram-selected files is not implemented |
| Explicit multi-selection and Done | Real webhook route with SQLite tests, revision/message binding | Live Telegram interaction against the intended deployed/local receiver and shared database |
| Combined Gemini summary | Real adapter code; mocked HTTP plus generated blank PDFs in the vertical slice | Verify free-tier eligibility, model/schema compatibility and real multi-PDF output; review scope, meaning, citations and inference quality |
| 1500–2500 Chinese characters | Local count checks BMP CJK characters in rendered Markdown | Agree on counting convention and verify actual generated output; current invalid-output behavior stops as uncertain rather than automatic regeneration |
| Firestore canonical notes | Isolated live note roundtrip, first-result preservation and claim completion passed | Verify actual worker/webhook use the same intended project/prefix and deployed credentials; live complete workflow remains absent |
| Read/filter/export notes | Local command routes; real synthetic Markdown sendDocument accepted | Live /notes and /note; deployed /export route; user viewing is not established by API acceptance |
| Automatic chunk delivery and failures | Per-chunk claims and deduplicated failure notices tested with mocks | Real multi-message delivery and failure recovery workflow; late-success/recovery cases need operational validation |
| Retry deduplication | SQLite races, local vertical slice, sequential live Firestore claims | Live concurrent execution and operational uncertain-outcome recovery; no exactly-once provider claim |
| Secret safety/read-only scope | No credential export, origin allowlist, isolated parser environment | Live extension compatibility, local trust model, retention limits and Windows parser memory bound remain limitations |

Next evidence sequence:

1. User supplies the public Chronos extension ID and confirms reload; use the
   existing TronClass tab and explicitly test one PDF receiver handoff.
2. Inspect the actual course listing/upload metadata. Implement only observed
   interfaces; unknown upload timestamps or incomplete traversal remain failures
   of the original catalog requirement, not accepted feature reductions.
3. Confirm Gemini free-tier/project billing constraints before a live request.
   Start with synthetic material, then user-selected course PDFs for semantic QA.
4. Run live selection, generation, Firestore readback, chunk delivery and export
   as one workflow using the intended shared database, then re-audit this table.

User inputs outstanding: installed extension ID/reload confirmation and Gemini
free-tier/billing confirmation. Do not ask for cookie values or API keys in chat.
Update: the user confirmed extension reload and accepted automatic viewing/download
progress records. The public extension ID remains outstanding. Free-tier-only is
the requested spending constraint, not evidence of project billing eligibility.

The extension now exposes `chronos.list_visible_activities` for courseware pages,
using observed `expandable-content-new` attachment IDs. It deduplicates numeric
IDs, rejects unsupported pages, and reports unknown for empty/over-limit results.
Every result retains `complete_course=false`. Sixteen JavaScript tests pass;
this is a discovery interface, not an installed-extension verification, automatic
traversal, attachment enumeration, or upload-time implementation.
The worker is not running merely because a launcher exists. No enabled companion
process or newly deployed Phase 2 webhook has been verified.

Courseware DOM follow-up: all five observed OS activities contained one PDF row
inside a matching `.attachments.attachments-<activity_id>` group, even while
collapsed. Reading this markup needed no expansion or preview. Filename and
extension were separate `.file-name` and `.file-extension` text nodes. This is
current-page evidence only, not proof of pagination completeness or upload dates.
`chronos.list_course_materials` now returns per-activity snapshots from those
groups, rejects ambiguous/missing containers as unknown, and retains
`complete_course=false`. Eighteen JavaScript tests pass. Popup/local handoff of
these multi-activity snapshots is not yet connected; the installed extension has
not been verified with this revision.

Popup integration now falls back from individual activity metadata to courseware
snapshots and sends each observed activity through the existing validated receiver
endpoint. Unknown snapshots are counted, partial failures stop the transfer with
an explicit warning that earlier items may already be saved, and metadata transfer
never starts a PDF download. Courseware download requests re-read the current DOM
catalog before using the existing bounded download handler. Twenty JavaScript
tests pass, including multi-activity handoff success and second-item rejection.
These mocked checks do not establish installed-extension or live receiver success.

## PRD acceptance scope

### Live catalog inspection — 2026-10-03

The existing authenticated Chrome tab was inspected through visible DOM only.
Navigation from the activity's course link to `content`, then the visible
`教材` link to `courseware`, succeeded. One navigation tool call timed out;
the next DOM snapshot confirmed success, so the action was not repeated.

The OS courseware page displayed five reference-file activities across two
chapter groups. This establishes a course-level discovery surface, not five
PDFs: activity attachment counts and file identities still need inspection.
The rendered sorting control contained chapter and title choices plus ascending
and descending order. No upload-date field or upload-date sorting choice was
observed. Absence here does not prove the server has no upload metadata.

Reproduction: open the authenticated course, choose 教材, inspect activity rows
and sorting controls. Do not click download or complete any activity to reproduce
these observations. No pagination completeness, extension handoff, authentication
renewal, or attachment-byte integrity was established by this inspection.

Next implementation evidence needed: rendered row structure and stable activity
identifiers, per-activity attachment enumeration, and an authoritative source of
upload dates. Do not infer timestamps from chapter order or local observation time.

Follow-up DOM inspection found `expandable-content-new="attachments-<activity_id>"`
on each of the five file-list controls. Expanding the Lecture 1 control exposed
one PDF attachment with a reference download link, filename and displayed size;
no upload date appeared. The DOM therefore supplies activity and reference IDs
without reading application-internal state or exporting authenticated URLs.

An attempted batch expansion of the remaining observed controls unexpectedly
opened a PDF preview overlay. Further interaction stopped. No explicit submission
or download action was requested, but viewing may affect server-side learning
progress; this run must not be described as proven side-effect-free. The five
activities were not fully enumerated. Before automating traversal, inspect and
verify each individual expansion result, and explicitly account for preview and
progress-tracking behavior. Stable DOM IDs alone do not prove safe interaction.

- Obtain course-specific PDF metadata through the approved browser-session route;
  newest uploads first. Preserve explicit reauthentication/deferred outcomes.
- Telegram multi-select with an explicit confirmation action, never infer consent.
- Combine selected verified PDF content with reported progress in Gemini; Chinese
  explanations with English terms, source files/pages, and clearly labeled inference.
- Save the canonical Markdown and required metadata/status in Firestore.
- Support notes retrieval, course filtering, note reading, Markdown export, split
  Telegram delivery, bounded retries and generation deduplication.
- No assignment submission, TronClass write, cookie export or paid-model fallback.

## Current implementation

`study_materials.py` provides verified-material catalog filtering/sorting, explicit
multi-selection and a generation fingerprint that includes content, progress and
model/prompt version. Repeated selection callbacks use set membership, not toggles.
The fingerprint is not a database lock and does not alone prevent concurrent jobs.

258 Python tests pass. The extension extracts visible activity PDF metadata and
has an explicit handoff button; the loopback receiver validates metadata and
accumulates isolated per-activity snapshots in memory. An HTTP regression test
confirms rejected extra fields cannot alter an accepted snapshot. Unknown
observations do not erase prior snapshots. This is not durable storage, a complete
course inventory, or freshness evidence.

`study_notes.py` validates structured summary drafts and rejects unknown source
IDs or out-of-range page citations using caller-supplied verified page counts.
Its Markdown renderer labels exam material as inference and renders model text
as data rather than active links. Seven tests cover these structural boundaries.
This does not verify factual entailment, obtain page counts, enforce the final
1500–2500 Chinese-character requirement, or call Gemini; those remain pending.

Live extension-to-receiver delivery, PDF byte retrieval, Telegram button
integration, Gemini summaries and notes storage remain unverified.

The extension now includes a bounded PDF GET handler for sources present in the
current visible activity catalog. Browser-managed credentials stay in Chrome;
redirects are rejected, transfers time out after 30 seconds, and streams above
12 MiB are cancelled. HTML responses fail the PDF envelope check. This check is
not a PDF parser. Fourteen JavaScript tests pass with mocked download responses;
no live download or local byte handoff is established by these tests. The handler
is extension-runtime-only and is not exposed through the page-facing probe.

The local `/v1/browser-pdf` endpoint now accepts bounded base64 PDF bytes only
for sources already in the received course catalog. It validates the byte count
and PDF envelope, computes SHA-256 itself, and atomically saves a hash-named file
under gitignored `.study-data/pdfs/`. Six storage tests and one HTTP integration
test cover repeat persistence and rejected input. These use synthetic envelopes,
not parseable lecture PDFs; parser validation, retention limits, strict extension
identity authentication and live browser evidence remain open.

After metadata acceptance, the popup offers per-file explicit download buttons.
It sends bytes to the local PDF endpoint and reports success only after a matching
persisted receipt. Two popup tests cover acceptance/rejection and ensure metadata
collection alone does not download files. Keep the popup open during transfer;
closing it can interrupt delivery and requires retry. This diagnostic entry point
is not the required Telegram multi-selection workflow or cloud synchronization.

## Integration gaps

The extension can export redacted observations and visible PDF metadata locally.
It does not yet provide course PDF metadata/bytes to the deployed service. A verified-material
catalog is only a downstream contract; real metadata must be discoverable before
download and deferred attachments must remain representable. Do not call the
feature complete using manual uploads or synthetic catalogs as a substitute.

Next: design and implement the authenticated browser-to-local material boundary,
durable selection persistence, then wire Telegram callbacks and summary jobs.

## Canonical note storage checkpoint

`NoteRecord` holds course/date/progress, selected source metadata and hashes,
Markdown, model/prompt version, completed status, creation time and fingerprint.
Failed generation attempts must remain job records rather than completed notes.
Firestore methods now save first-result-wins in a transaction, retrieve by full
fingerprint and list/filter notes. Markdown export returns UTF-8 bytes without
depending on Cloud Run local files. Six tests cover records, export and a fake
Firestore retry; no live notes write has been performed. This does not prevent
duplicate model calls, verify semantic grounding, or integrate Telegram commands.
List queries currently sort after retrieval and need bounded server-side queries
and indexes before a large archive. SQLite now implements the same note API with
indexed, bounded queries and conflict-safe insertion. A shared repository test
runs against SQLite and fake Firestore; a separate eight-writer SQLite test
verifies retries preserve one canonical record. This is not live Firestore
concurrency evidence or generation-job deduplication.

## Generation claim checkpoint

`SummaryJobs` now uses dedicated SQLite/Firestore job records. One claim may run
per fingerprint; an interrupted ten-minute claim becomes uncertain rather than
automatically restarting. Explicit rejection permits up to three total attempts,
five minutes apart. Completion requires an existing canonical note. Five tests
cover SQLite/fake-Firestore transitions and an eight-writer SQLite claim race.
This ledger is not yet wired to Gemini or the scheduler, and cannot itself prove
that external model execution is exactly once. The caller must classify errors
conservatively and persist the note before reporting completion.

## Read commands checkpoint

`/notes [course]` and `/note <full fingerprint> [page]` are routed through the
existing message handler. Listings return at most ten notes; reading preserves
all Markdown using bounded plain-text pages with an explicit next-page command.
Unicode tests cover astral characters without splitting code points. Tests use
the real SQLite repository and command handler, not a live Telegram webhook.
This browsing interface does not satisfy automatic summary chunk delivery;
automatic delivery and `/export` document delivery remain pending.

## Export integration checkpoint

`/export <fingerprint>` now branches after webhook owner authentication and sends
canonical UTF-8 Markdown through multipart `sendDocument`. A per-update delivery
claim prevents blind resends after success or uncertain transport. Explicit 4xx
rejections use the existing bounded retry ledger. Three service tests cover
success, timeout and rejection using a real SQLite ledger and mocked Telegram.
This supersedes the earlier pending export implementation note; live Telegram
document delivery remains pending. Five webhook integration cases now verify
owner/secret rejection before note reads, missing-note feedback, duplicate update
handling and uncertain-send suppression, with the real SQLite repository and a
mocked Telegram sender. Automatic
summary chunk delivery is still unimplemented.

## Summary delivery service checkpoint

`deliver_summary` now splits immutable canonical Markdown into ordered plain-text
messages. Per-chat, fingerprint, chunk-version and index receipts skip confirmed
chunks on retry; explicit rejection stops until retry eligibility, while unknown
outcomes stop without automatic resending. Three SQLite-backed service tests
verify full 6,500-character preservation, middle-chunk resume and uncertain-stop
behavior with mocked Telegram. This supersedes the missing chunk-service note
above, but generation completion and scheduler integration remain pending; no live
automatic summary delivery has been verified.

## Durable selection checkpoint

SelectionStore serializes course catalogs, explicit selected IDs and confirmation
to dedicated SQLite/Firestore records. Chat ownership and optimistic revisions
prevent stale callbacks from changing newer choices; empty confirmation is
rejected and confirmed selections are immutable. Four shared adapter tests cover
round-trip persistence, stale events, confirmation and ownership/source rejection.
Telegram inline button routing and catalog ingestion are not yet connected.

Inline keyboard rendering and callback routing are now implemented, with eight
files per page, explicit select/unselect membership and a Done action. One HTTP
test covers two-file selection, a duplicated event and confirmation against
SQLite with mocked Telegram edits. Initial catalog-message delivery, callback
message binding, generation dispatch and live interaction remain pending; this
supersedes only the missing callback-routing item above.

Selection prompts now have a claimed send service and immutable message binding.
Callbacks from any other message are rejected. A saved successful receipt can
restore interrupted binding without resending. One SQLite-backed test covers this
recovery plus incorrect-message rejection. The service still needs real catalog
ingestion and confirmed-selection generation dispatch; no live prompt was sent.

## Summary coordination checkpoint

`generate_selected_summary` connects confirmed selection, exact source-set/hash
checks, generation claims, structured citation validation, canonical persistence
and chunk delivery. It checks 1,500–2,500 BMP CJK characters in rendered Markdown
(including headings/citations); this counting convention needs product review.
Two SQLite-backed tests use fake generator/Telegram and synthetic parser inputs
to verify persistence before send, no regeneration on retry and no delivery of
too-short drafts. Real PDF parsing, Gemini implementation, dispatch from confirmed
callbacks and live integration remain pending. Invalid output becomes uncertain
and is not regenerated automatically; manual recovery is not implemented yet.

## Gemini adapter checkpoint

`GeminiSummary` builds one generateContent request containing selected inline PDF
bytes and a structured SummaryDraft schema. It uses only the configured model,
approved Google endpoint and study-v1 prompt, rejects incomplete responses, and
leaves retries to the durable job ledger. Aggregate raw PDF input is capped at
12 MiB. Five mocked HTTP tests cover request structure, output validation,
failure classification and default no-network gating. Free-tier eligibility must
be confirmed explicitly before use; the flag is an operator assertion, not a
billing guarantee. No live Gemini request or PDF upload has occurred.

API references checked: https://ai.google.dev/api/generate-content and
https://ai.google.dev/gemini-api/docs/structured-output . Live schema compatibility
and actual model availability still require verification.

## PDF parser checkpoint

The generation pipeline now validates PDF structure with pypdf 6.19 and compares
the actual page-tree count with supplied metadata before claiming generation.
Malformed envelopes, encryption, empty page trees and over-limit page counts are
rejected with generic errors. Four parser tests and updated pipeline fixtures use
real generated blank PDFs, not lecture content. This proves structural parsing,
not rendering quality, semantic evidence or live lecture download. Parsing is
currently in-process: process isolation/time-memory enforcement remain required
before exposing this path to untrusted production files. Local persistence still
uses envelope checks; structural verification is enforced at generation entry.

Generation entry now uses an isolated Python parser subprocess with a ten-second
wall timeout, sanitized environment, suppressed diagnostics and no shell. The
async pipeline runs it off the event loop. A real Windows subprocess test reads
a generated PDF; another test checks timeout classification and credential-env
exclusion. POSIX CPU/address-space limits are implemented but not tested here.
Windows hard memory limits remain missing. This is process isolation, not a
security sandbox: filesystem/network capability restrictions are not enforced.

## Local attachment index checkpoint

PdfStore now persists course/source identity, filename, activity, hash, byte count
and local save time in a gitignored SQLite index beside the blobs. Reads require
the expected hash and revalidate bytes; changed, missing or cross-course sources
fail closed. File replacement precedes index commit, so interruption can leave an
orphan blob but not a partially written indexed file. A restart/corruption test
passes. Local save time is not upload time, the saved subset is not a complete
course catalog, and this does not establish browser-to-cloud synchronization.

## Local summary input checkpoint

`generate_local_summary` now resolves only explicitly confirmed selections from
the persistent PDF index, verifies bytes and parser page counts, and feeds the
summary pipeline. Missing, renamed or already-hash-bound changed attachments
return deferred_attachment before model invocation. Two tests use actual generated
PDFs and cover selected-only loading, changed content and missing-file refusal.
First-resolved hashes are not yet written back to durable selection state, so the
caller still needs a freeze step before cross-process generation retries. This is
not cloud synchronization or automatic callback-to-generation dispatch.

The local generation entry now requires a durable selection key, reloads the
authoritative saved selection, and transactionally freezes first-resolved hashes
before generation. Two adapter tests reject different hashes, changed progress
and another chat while allowing identical retries. This closes the missing hash
freeze step above, but does not implement dispatch or real-data verification.

## Companion dispatch checkpoint

`run_summary_pass` connects saved confirmed selections to local generation using
the authoritative answered course session and an explicit course-ID mapping.
It checks owner, progress and course context, records outcomes and skips completed
or terminal work. Default execution is disabled. Two shared-adapter tests verify
dispatch context and completion skipping with mocked generation. This is a worker
function, not an activated daemon or deployment. Full collection scans, fairness
for repeatedly deferred selections, recovery policy and real-data wiring remain
unfinished; no live model requests or Telegram messages were sent by this worker.

The worker now has a launcher: `.venv\\Scripts\\python.exe -m
chronos.run_summary_companion`. Running it without arguments was verified to
return `summary_companion=disabled` without initializing providers. Enablement
requires `--enable`, `--free-tier-confirmed` and `--course-map` containing a JSON
object of known schedule keys to verified numeric TronClass IDs. `--watch` repeats
every 30 seconds; without it there is one pass. `--pdf-directory` selects the local
blob store. Never treat the eligibility flag as a billing guarantee. Six launcher
tests check safe defaults and mapping validation. The launcher has not been run
enabled. It must use the same database project/prefix as the webhook. Oldest-attempt
ordering now prevents a fixed first batch of deferred records monopolizing passes;
full scans and durable retry scheduling still need improvement.

## Shared browser metadata checkpoint

The receiver CLI now persists activity metadata to `.study-data/catalog.sqlite3`
by default (`--catalog-path` overrides it). MaterialObservationStore can read the
same database from another process and returns observed_at plus explicit
complete_course=false for snapshots. One shared-instance test verifies durable
reads, unknown-response preservation and rejected-secret-field isolation. This
does not establish catalog freshness, complete traversal or genuine upload dates;
selection prompting must preserve these limits instead of substituting save time.

The enabled companion now invokes observed-catalog prompting before generation.
Answered sessions with mapped course IDs and snapshots no older than 24 hours
receive a stable, deduplicated selection prompt. Nothing is preselected. The text
explicitly states incomplete course coverage and unknown upload-time ordering.
Two shared-adapter tests cover absent data, prompt creation and duplicate
suppression with mocked Telegram. This is an incremental integration path, not
acceptance of the PRD's complete, newest-upload-first catalog requirement. A later
fresh snapshot does not automatically refresh an already delivered selection.

## Local vertical-slice evidence

`tests/test_phase2_integration.py` exercises persisted browser-format metadata and
PDF bytes, catalog prompting, authenticated selection callbacks, companion
dispatch, isolated PDF parsing, Gemini request serialization/response validation,
canonical SQLite storage, summary delivery and authenticated Markdown export.
It verifies one provider call across worker retries and one attachment send across
duplicate export updates. The full suite passes 250 tests. The PDF is generated
blank content and the Gemini/Telegram networks are mocked: this proves internal
integration, not real browser transfer, semantic quality, Firestore production
behavior, free-tier eligibility or live Telegram delivery.

## Live Markdown export evidence — 2026-10-03

Ran `python -m scripts.verify_study_export` using the configured Chronos owner
chat. It created a temporary SQLite canonical note containing only clearly
labelled synthetic UTF-8 text, then invoked the real export service and Telegram
client. Result: canonical_export_confirmed=true, duplicate_suppressed=true,
delivery_status=sent. The repeat invocation replaced network sending with a
failure guard and completed without invoking it. Temporary synthetic database
files were removed automatically; the test attachment remains in Telegram.
No lecture content, credentials, cookies or private note contents were sent.
This confirms Telegram API acceptance, not human viewing or production webhook
deployment. Do not blindly rerun the verifier after an unknown delivery outcome.

## Live Phase 2 Firestore evidence — 2026-10-03

Ran `python -m scripts.verify_phase2_firestore` with existing local gcloud
credentials captured only in memory. A fresh `study_phase2_verify_<UUID>` prefix
isolated three synthetic documents from production records. Observed results:
note_roundtrip=true, first_result_preserved=true, selection_roundtrip=true,
job_completed=true and cleanup_complete=true. A second database client read back
the note and selection; the test also checked exclusive sequential claims and
completion after canonical persistence. Exactly the three test documents were
deleted and absence verified. No real course data or secrets were stored in them.
This proves real adapter read/write behavior, not concurrent load safety, deployed
service-account permissions, complete catalogs or full live summary generation.

## Failure reporting integration

The pipeline now reports persisted retry/failed/uncertain job status rather than
collapsing every unsuccessful claim into generation_pending. The companion sends
deduplicated generic owner notices for uncertain, exhausted or context-mismatched
work, using the delivery ledger; unknown notice outcomes are not blindly resent.
Two shared-adapter notice tests and strengthened pipeline assertions pass within
the 252-test suite. This is local integration evidence, not a live failure test.

## Receiver origin restriction

The receiver now requires `--extension-id <installed-Chronos-ID>` when launched
from the CLI and accepts only that exact chrome-extension Origin. Other extension
origins, prefix lookalikes, websites and null origins are rejected; accepted
responses include matching CORS headers. Six tests bring the full suite to 258.
Example launch: `.venv\\Scripts\\python.exe -m chronos.local_observation_server
--extension-id <32-letter-installed-ID>`. The ID is public, not a secret. Requests
without Origin remain allowed for trusted local tools; Origin checks do not
authenticate arbitrary local processes. Live installed-extension interoperability
still requires the actual ID, reload and explicit popup test.
