# Phase 2 — active implementation

## Acceptance audit — 2026-10-03

**Not accepted.** Component implementation and local integration do not prove the
requested live workflow. The chronological checkpoints below are historical;
this requirement-level audit takes precedence when interpreting completion.

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
