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

163 repository tests pass, including four material-selection tests. No Phase 2
browser retrieval, Telegram button integration, Gemini summary or notes storage
has been verified yet.

## Integration gaps

The extension currently exports redacted page observations only. It does not yet
provide course PDF metadata/bytes to the deployed service. A verified-material
catalog is only a downstream contract; real metadata must be discoverable before
download and deferred attachments must remain representable. Do not call the
feature complete using manual uploads or synthetic catalogs as a substitute.

Next: design and implement the authenticated browser-to-local material boundary,
durable selection persistence, then wire Telegram callbacks and summary jobs.
