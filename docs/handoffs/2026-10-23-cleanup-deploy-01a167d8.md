# Event cleanup deployment and exam color repair

- Updated: 2026-10-23 (Asia/Taipei).
- Authorization: user requested correction and deployment after reviewing PR #30 and the existing red OS exam.
- Source: merged 79e8086916d5d699265f29578625a4ee35e6e79c. Exact merge CI 39927159857 passed Python 3.11, Python 3.12 and extension before deployment.
- Scope: clean-source deploy, preserve runtime config, validate candidate before traffic, repair only the uniquely matched Operating Systems Midterm on 2026-10-30 09:20–12:00 from red to purple with an ETag guard. No event creation or historical message deletion.
- Completed: chronos-cleanup-79e8086 serves 100% traffic. Public /ready reports ready, Firestore and exact SHA; authenticated status confirms matching release and configured Telegram/Calendar/Study, Gmail disabled. Candidate environment differs only in release SHA. Baseline ancestry and traffic guard passed; rollback chronos-exam-3892735.
- Existing event repair: exactly one matching owned exam at the screenshot date/time was patched from color 11 to color 3 using its ETag. Readback confirmed purple and unchanged title, start/end, description, private metadata, recurrence, attendees, visibility and transparency. No event created.
- Evidence: sanitized local .tmp/cleanup-release-79e8086.json and .tmp/cleanup-exam-repair.json. No credentials or private event IDs published.
- Validation boundary: merged CI and deployed source identity verified; webhook deletion behavior is covered by integration tests, not a synthetic live Telegram conversation. No unrelated Calendar/task changes or Telegram test messages.
- Status: deployment and requested color repair complete; evidence publication pending. Current source-message deletion applies to newly bound requests, not legacy cards lacking a source ID.

- Updated: 2026-10-23T16:51:47+08:00.
