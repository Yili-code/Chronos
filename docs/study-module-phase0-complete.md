# Chronos Study Module — Phase 0 Feasibility Complete Report

Date: 2026-10-02  
Scope: read-only feasibility spike only

## Executive conclusion

Phase 0 is complete for the tested scope. The safe integration direction is a browser-mediated, read-only adapter: Chrome owns CAS authentication; the extension sends only redacted page observations to a loopback Chronos process; the adapter returns explicit states instead of guessing.

The REST CAS route is not accepted as a reliable implementation. Two credential attempts obtained CAS TGT and service tickets, but both ticket-to-TronClass exchanges ended at the CAS login page. Browser-mediated access did expose the expected TronClass course data, announcements, assignments, deadlines and attachments.

## Goals and verdicts

| Goal | Verdict | Evidence and limit |
|---|---|---|
| CAS account/password login | REST route unreliable; browser login viable | 2/2 REST attempts returned to CAS login; browser session reached authenticated TronClass |
| Courses, announcements, assignments, deadlines, attachments | Read-only retrieval feasible through browser | Observed in the authenticated TronClass UI; no write action performed |
| PDF download and persistence | Passed for one tested PDF | 5 downloads across 2 browser profiles were byte-identical; broader attachment coverage remains open |
| Session lifecycle | Reuse observed, exact expiry unknown | Reopen/reload stayed authenticated at 30s and 60s; cookies, CSRF tokens and headers were never read |
| Official calendar parsing | Fail-closed rules feasible | Explicit holiday, make-up holiday, normal instruction and exam-period classifications were tested; ambiguous text requires confirmation |
| Secret safety and side effects | Passed for the tested code path | No password, cookie, token, CSRF value or bearer URL was persisted; no assignment submission or TronClass mutation |

## Observed login and retrieval flow

1. User opens TronClass and is redirected to the NTOU CAS login page.
2. User completes login in Chrome; Chrome retains the browser session.
3. TronClass authenticated pages expose course navigation and read-only content.
4. The extension classifies the current tab using safe URL/path and page-marker observations.
5. The local bridge accepts only allow-listed observations from the extension.
6. The browser-session adapter maps observations to `READY`, `REAUTH_REQUIRED`, `UNKNOWN`, or `UPSTREAM_UNAVAILABLE`.
7. Read operations return `OK`, `REAUTH_REQUIRED`, `UNKNOWN`, or `DEFERRED_ATTACHMENT`; they do not silently convert failures into empty data.

## PDF persistence evidence

Tested activity: `作業系統 / Lecture 0: Course Info and Course Introduction`  
Tested attachment: `Lec0_Course Info & CourseIntroduction_OS .pdf`

| Sample group | Count | Result |
|---|---:|---|
| Browser profile A | 4 | valid PDF, 466,300 bytes, 14 pages, PDF 1.7 |
| Browser profile B | 1 | valid PDF, 466,300 bytes, 14 pages, PDF 1.7 |
| Combined | 5 | same SHA-256: `fd31bde477c7c1e3b00e56139f1e37019b542268e7bb51fa9e4e1b888f1d3a14` |

The gate is therefore **verified for this attachment**, not for every TronClass attachment. Any future sample that fails magic-byte, EOF, parser, size or checksum checks must be classified as deferred rather than passed to a summary pipeline.

## Session and security findings

- A CAS service ticket is not proof that a TronClass application session exists.
- HTTP 200 is transport evidence, not authentication evidence.
- The adapter intentionally does not inspect or persist cookies, local storage, CSRF tokens, authorization headers, passwords or full response bodies.
- A CAS redirect maps to `reauth_required`; a timeout or unrecognized page maps to `unknown`.
- Diagnostics use allow-listed operation names and retryability only.
- The system performed no assignment submission, content edit, attendance action or other TronClass write.

## Recommended adapter design

```text
Chrome tab
  └─ Chrome extension (user click, read-only observation)
       └─ loopback HTTP bridge (allow-list + redaction)
            └─ BrowserSessionAdapter
                 ├─ session(): SessionState
                 ├─ list_courses()
                 ├─ list_announcements()
                 ├─ list_assignments()
                 ├─ list_attachments()
                 └─ download_attachment()
```

The connector interface should stay narrow: current safe URL/path, safe visible markers, and transport failure. Authentication remains in the browser. Attachment persistence is a separate gate, so metadata visibility cannot be mistaken for durable bytes.

## Risks and open questions

1. Exact cookie/session expiry and renewal timing is unknown because credential material was deliberately not inspected.
2. Other attachment types, larger PDFs, and long-lived browser sessions still need separate evidence.
3. CAS/TronClass page changes may invalidate markers; the safe response is `unknown` plus maintenance signal.
4. The extension bridge is a local privilege boundary and needs continued permission and origin review.
5. Full pytest remains environment-blocked when optional runtime dependencies such as `fastapi` and `apscheduler` are absent; focused Phase 0 tests are the current evidence.

## Reproducible evidence

- Feasibility report: `docs/study-module-phase0-feasibility.md`
- PDF evidence matrix: `docs/study-module-pdf-repeatability-evidence.md`
- Browser adapter contract: `docs/study-module-browser-session-adapter-design.md`
- Connector and state model: `chronos/study_adapter.py`
- PDF integrity checks: `chronos/pdf_persistence.py`
- Focused tests: `tests/test_study_adapter.py`, `tests/test_pdf_persistence.py`, `tests/test_chrome_bridge.py`, `tests/test_local_observation_server.py`, `tests/test_tronclass_auth_spike.py`, `tests/test_study_calendar_spike.py`

## Phase gate

Phase 0 may advance to Phase 1 with one explicit constraint: Phase 1 must use the browser-session boundary and preserve the `deferred_attachment` fallback. It must not revive the unproven REST login path or add TronClass write operations.
