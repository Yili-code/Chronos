# Mail correspondence implementation

- Updated: 2026-11-03 Asia/Taipei.
- User resumed the executive-assistant upgrade; LINE/Instagram remain deferred. Deployment authorization persists but core acceptance gates remain mandatory.
- Branch: codex/executive-assistant-audit; baseline 6effb2d1797e26e7fb41320b1b3e419c6db51f53, PR #124. Original workspace changes preserved.
- Read PROJECT_STATUS, prior executive audit handoff, Gmail/mail workflow, webhook contracts and database mutation interfaces. No overlapping active edit identified.
- Scope: full-thread context, reviewed local reply drafts, explicit Gmail draft saving (never sending), reply-state tracking with provenance.
- Decisions: AI never selects recipients or calls external tools. Native draft saving is distinct from local review and sending. Recheck source-thread version, persist uncertain creates, and never automatically replay them. Attachments/BCC/reply-all require separate recipient/attachment review and will be disclosed as unsupported.
- Official Gmail draft/thread/drafts.create references checked. Existing gmail.modify scope permits draft creation; no Contacts scope assumed.
- Validation/commit/push/deployment: pending for this continuation. Next: typed transport/state workflow, concurrency and failure tests, full regression and CI. Other domains remain unfinished.

## Correspondence validation — 2026-11-03 Asia/Taipei

- Implemented full-thread read, account-bound local drafts, explicit token-bound Gmail draft creation, stale-context rejection, uncertain-write preservation and no send API. Typed AI output never selects recipients; incomplete context or unsupported attachment/CC/BCC/reply-all requests are explicitly rejected.
- Fixed a concurrency issue during development: prepared revisions now compare both version and state, preventing them from overwriting an in-flight native save.
- Implemented email reply-state registry, opposite-side reply resolution, auto-reply pauses, explicit reminder timing, one owner-only notification per cycle, and daily refresh. Notification claims and existing delivery ledger prevent automatic uncertain replay. Daily scheduling resolution and provider-read/send race are disclosed.
- Real Telegram webhook tests verify preview reply binding, duplicate receipts, identity guards and cancelling tracking. Both SQLite and Firestore fake participate in workflow tests.
- Validation: 96 newly added correspondence tests passed; complete Python suite **3109 passed**, 1 existing Starlette/httpx deprecation warning, 175.43 seconds. Undefined-name lint passed. Tests are synthetic, not live Gmail draft or Telegram delivery evidence.
- Contacts/relationships module and its 32 passing component tests were developed separately after full-suite collection. They are not yet wired into production handlers and are not part of the preceding 3109 result or correspondence commit.
- No production mutation or live draft creation. Plan: commit/push the validated correspondence change, then connect and validate contacts/relationships and continue meeting workflow implementation. Existing overall acceptance gate remains incomplete.
