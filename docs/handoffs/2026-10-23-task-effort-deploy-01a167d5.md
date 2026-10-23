# Task effort production release and targeted repair

- Updated: 2026-10-23T16:33:00+08:00 (Asia/Taipei).
- Authorization: user explicitly requested deployment and correction of the screenshot task.
- Source: merged 1fc97d9f16e3ac663b35a4de69c1ee0aacae7090; exact push CI 39924665897 passed required Python 3.11, Python 3.12 and extension jobs.
- Production: yili-chronos-prod / asia-east1 / chronos; revision chronos-audit-1fc97d9 at 100% traffic. Public readiness and authenticated internal status report matching release, ready Firestore and configured Telegram/Calendar.
- Deployment: clean Git archive, candidate at zero traffic before readiness and configuration validation; only release SHA environment entry changed. Baseline ancestry and unchanged production traffic checked before promotion. Rollback revision chronos-audit-6072775.
- Targeted repair: inspected the authorized screenshot task, moved its effort-only content into estimated_minutes=120 with an update-time precondition. Firestore readback matched expected fields exactly; title and every unrelated field preserved. Existing source text retained. Deployed-source task list formatter confirmed no visible Content or effort text. No Calendar writes or synthetic Telegram delivery.
- Validation boundary: production version/readiness, Firestore repair and deployed-source rendering verified. Additional live Gemini test was rejected by automatic approval review because it would retrieve production AI credentials and send task text externally; it was not run or bypassed. Existing provider-mock tests and exact merge CI remain the parsing evidence. Historical Telegram messages are not edited.
- Status: requested deployment and existing-data correction complete. Evidence publication pending; no remaining runtime work. Original checkout contains unrelated modifications that remain excluded.
