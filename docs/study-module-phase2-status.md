# Phase 2 — active implementation

## PRD acceptance scope

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

246 Python tests pass. The extension extracts visible activity PDF metadata and
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
