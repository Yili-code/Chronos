# Phase 0 PDF Repeatability Evidence

This record contains only redacted file-integrity evidence. It does not contain
cookies, headers, CSRF values, bearer URLs, or response bodies.

## Tested attachment

- Course activity: `作業系統 / Lecture 0: Course Info and Course Introduction`
- Attachment name: `Lec0_Course Info & CourseIntroduction_OS .pdf`
- Download endpoint shape: `/api/uploads/reference/<id>/blob`
- Browser profile A: 4 downloads, including one after a full activity reload
- Browser profile B: 1 download

## Integrity result

| Samples | Size | PDF version | Pages | SHA-256 | Result |
|---:|---:|---:|---:|---|---|
| 5 | 466,300 bytes | 1.7 | 14 | `fd31bde477c7c1e3b00e56139f1e37019b542268e7bb51fa9e4e1b888f1d3a14` | `verified` |

All five files had a valid `%PDF-` signature, a valid EOF marker, the same
byte count, and the same checksum. The browser downloaded each file without a
visible CAS redirect or timeout.

## Scope limit

This verifies one PDF attachment across two connected Chrome profiles. It does
not prove that every course, file type, or future session will behave the same.
Unknown attachments must remain `browser-only` or `deferred_attachment` until
they pass the same checks.
