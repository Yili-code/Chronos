# Chronos Study Module — Phase 0 Feasibility Report

Date: 2026-10-01

This report records the Phase 0 feasibility spike only. It does not implement
the Study Module or perform any TronClass write operation.

## Status summary

| Goal | Status | Evidence / limitation |
| --- | --- | --- |
| CAS account/password login | Partial / adapter route rejected | The credential probe completed two attempts; CAS issued a TGT and service ticket on both attempts. TronClass rejected both ticket-to-session exchanges and redirected back to CAS login. Browser-mediated manual login still works. |
| Courses, announcements, assignments, deadlines, attachments | Confirmed read-only | The authenticated UI exposed course navigation, announcement HTML and links, assignment status/type, deadline ranges, and attachment names/sizes/download links. |
| PDF download | Single-sample confirmed; repeatability deferred | The authenticated endpoint produced a local PDF file. The file was 522,673 bytes, began with `%PDF-`, parsed as PDF 1.7 with 13 pages, and rendered successfully. Only one successful persistence sample was verified; repeat-download stability is still a Phase 1 gate. |
| Session reuse | Confirmed for the observed session | The authenticated session survived navigation from the `tcmedia` PDF tab back to TronClass, a full page reload, and a five-second wait followed by another reload. Expiry and refresh lifetimes were not measured. |
| Cookie / CSRF lifecycle | Not inspected | Cookie values, headers, tokens, and browser storage were intentionally not read or logged. Their exact lifetime and renewal rules remain unknown. |
| Official academic calendar parsing | Confirmed for the sampled source | The calendar probe parsed 103 dated events from the official NTOU academic-calendar page and found normal instruction, no-class/holiday, exam-period, and confirmation-needed cases. |

## Observed authenticated flow

1. The browser reached NTOU CAS and the user completed login manually.
2. CAS redirected to TronClass.
3. TronClass displayed the student identity and course dashboard.
4. A separate secret-safe REST probe ran twice with the same credentials. Both
   attempts received a CAS TGT and service ticket, but both ended at the CAS
   login path instead of an authenticated TronClass path.
5. A course page exposed separate routes for chapters, announcements,
   courseware, homework, exams, forums, questionnaires, and classroom activity.
6. The homework view showed both assignment type and a concrete time range,
   for example `2026-09-30 12:30 ~ 2026-10-07 23:59`.
7. Announcement pages rendered rich text, external links, update timestamps,
   and attachment metadata.
8. Reference files exposed an authenticated `/api/uploads/reference/.../blob`
   link that redirected/opened a `tcmedia` PDF URL.
9. Lifecycle checks did not inspect cookie values, CSRF tokens, local storage,
   or headers. The session remained authenticated after the observed reloads,
   but no logout or forced-expiry action was performed.

No assignment submission, data edit, message, attendance action, or other
TronClass write operation was performed.

## Recommended adapter boundary

Use a read-only adapter with these explicit stages:

1. `CasSessionProvider`: prefer a user-mediated browser session for now. The
   tested REST ticket exchange can obtain CAS tickets but did not establish a
   TronClass session twice, so it must not be treated as a production login
   path until the service-side rejection is explained.
2. `TronClassClient`: request only known read endpoints and preserve the
   session transport internally.
3. `CourseMapper`: normalize course identity and links to announcements,
   courseware, homework, and deadlines.
4. `AttachmentResolver`: retain metadata and fetch bytes only when explicitly
   requested; validate status, content type, size, and checksum before storing.
5. `CalendarSource`: parse the official calendar into typed events while
   retaining `needs_confirmation` for ambiguous rows.

The adapter should fail closed on an expired session, an unexpected redirect
to CAS, an unknown response shape, or an attachment whose content type does
not match the declared file.

### Adapter decision

The evidence supports designing (but not yet fully implementing) a
browser-session adapter. Its contract should be read-only and user-mediated:
the user logs in in Chrome, Chronos reuses the browser-owned session through a
connector, and any redirect to CAS becomes an explicit `reauth_required`
state. Chronos must not collect or persist the password, cookies, or CSRF
tokens. The REST credential adapter remains rejected pending a documented
explanation for the repeated TronClass redirect.

## Remaining risks and decisions before Phase 1

- Whether automation may use a credential-based CAS flow at all, or must rely
  on a user-owned browser session.
- Why TronClass redirects valid CAS-ticket attempts back to CAS login; likely
  candidates include required browser state, service-string details, or an
  additional anti-automation check, but this remains unconfirmed.
- CAS ticket, TronClass session, CSRF token, and cookie expiration/renewal
  behavior.
- Exact session expiration and renewal timing; current evidence only covers
  reload and short-delay reuse.
- Whether PDF downloads require an additional token, referer, or browser-only
  behavior that a server-side client cannot reproduce.
- Whether repeated PDF downloads remain stable across a fresh browser session;
  until that is demonstrated, attachment persistence remains deferred for
  Phase 1. Browser viewing is the safe fallback.
- Stable read endpoints and pagination semantics across courses and semesters.
- Whether announcement HTML and external links should be stored verbatim,
  sanitized, or reduced to plain text.
- Retention, encryption, and deletion policy for downloaded attachments.
- A deterministic test fixture or redacted capture set that can be committed
  without exposing account data or secrets.

## Reproducible evidence

- `scripts/spikes/calendar_probe.py` and
  `tests/test_study_calendar_spike.py` cover the official calendar sample.
- `scripts/spikes/tronclass_auth_probe.py` and
  `tests/test_tronclass_auth_spike.py` provide secret-safe CAS ticket/session
  validation helpers and redaction tests. The probe now performs two
  independent CAS-to-TronClass attempts from one hidden credential input;
  the live run issued CAS tickets twice but failed both TronClass session
  checks. Only redacted status fields were retained.
- The live browser observation was performed read-only on 2026-10-01. It is
  evidence of the current account/session and site behavior, not a guarantee
  that future sessions or server-side requests will behave identically.

## Conclusion

Phase 0 is **not fully complete**. The core read-only TronClass surface and
short-term browser session reuse are feasible, and the official calendar path is feasible. The
tested credential-based REST route is currently **not reliable for TronClass
session establishment**: CAS ticket issuance passed twice, but both
end-to-end exchanges returned to CAS login. PDF persistence passed for one
sample but repeatability is deferred. Remaining blockers are a repeat-download
test and measured expiration/renewal behavior. A browser-session adapter is the
recommended design direction, but Phase 1 should not start until those gates
are closed.
