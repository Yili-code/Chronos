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

201 Python tests pass. The extension extracts visible activity PDF metadata and
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
