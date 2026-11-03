# Mail correspondence

Implemented after the initial executive-assistant baseline audit on 2026-11-03. Implementation and mocked regression evidence do not establish production deployment or live Gmail draft creation.

## Reply review

Reply to a delivered Gmail card with `草擬回覆：英文、簡潔，不承諾日期` or `draft reply: politely decline without inventing a reason`. Chronos verifies the configured account and loads the full available Gmail thread, rather than drafting from a snippet. Oversized, incomplete, automatic, self-sent or identity-ambiguous context is rejected. A conflicting Reply-To is not silently selected.

AI produces only body text and clarification questions. Recipient, original subject and reply headers are derived from the bound thread. Missing information keeps the draft in `needs_information`. A complete generated reply is stored locally as `review`; no native Gmail write occurs at this point. The owner must review its factual accuracy and commitments. Prompt grounding and review are not proof that every model output is factually correct.

The preview displays the recipient, subject, body and an eight-character confirmation code. Reply `儲存 Gmail 草稿 <code>` to explicitly save that exact reviewed version. Chronos reloads the thread and rejects stale, cancelled, expired or superseded versions. The resulting Gmail draft is unsent. Sending requires the separate [explicit authorization workflow](authorized-mail-send.md). Attachments, CC/BCC and reply-all currently require manual handling and are not silently omitted from a requested supported action.

Draft states are `needs_information`, `review`, `saving`, `gmail_draft`, `uncertain`, `stale` and `cancelled`; sending adds its own reviewed/pending/accepted/uncertain states. Concurrent saves claim the same version atomically. Network ambiguity, 5xx, malformed success results or interruption after the save claim leave a state requiring Gmail reconciliation, not an automatic retry. Definite API rejection can return to review. Cancelling a local draft never claims to delete an already-created native draft.

Draft previews and status replies remain bound to the original account/card through Telegram; ordinary task-title proposals remain a separate workflow.

## Reply tracking

Reply `追蹤回覆` to record the current thread direction without scheduling a reminder. `追蹤回覆：3 天` or `track reply: 24 hours` sets a single owner reminder after that interval, checked by the next mail scheduler run. Hour-level input does not make the daily scheduler run hourly. Supported intervals are one hour through 90 days.

The latest real message and SENT label/configured owner determine `waiting_owner` versus `waiting_other`. A later message from the opposite side resolves that particular wait. A new message from the same side restarts the timer; automatic replies pause it rather than count as a human response. API failures suppress reminders from stale state. `查看郵件追蹤` refreshes state without sending a reminder. `停止追蹤` stops future reminders; an already-started notification may still arrive.

Daily mail processing refreshes tracking even if that day's digest was previously completed. Before an overdue notification, Chronos re-reads the thread, claims notification state and uses the existing delivery journal. Only the configured owner chat is notified; no follow-up email is sent. One reminder is delivered per tracking cycle, without repeated chasing. Ambiguous delivery pauses the cycle. Human replies arriving after the final provider read can still race notification; Gmail and Telegram do not share a transaction.

Tracking is email-thread based. It does not infer cross-platform identity, distinguish every type of refusal/negotiation, or ingest arbitrary personal inboxes. A refusal arriving as a human response resolves the wait, but recipient-wide outreach preferences are maintained separately.

## Persistence and validation

State uses account-namespaced existing JSON documents: `reply:<bound-message-id>` and one `followups` registry. The registry is bounded at 100 tracked threads. No database schema migration is required. Multi-step native creates and Telegram sends occur outside database transactions; state transitions are transactional and compatible with SQLite and Firestore's read-before-write requirement.

Focused transport, workflow and real webhook tests cover full-thread input, account isolation, injection-safe MIME headers, incorrect recipients, stale context, duplicate receipt delivery, simultaneous saves, concurrent draft revisions, permissions, ambiguous creates, reply direction, auto replies, cancellations, notification claims and both persistence backends. All network boundaries in these tests are synthetic; they send no real messages.

API contracts: [Gmail drafts](https://developers.google.com/workspace/gmail/api/guides/drafts), [Gmail thread management](https://developers.google.com/workspace/gmail/api/guides/threads), [drafts.create](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.drafts/create). The existing Gmail modify grant supports native draft creation; the application still validates the actual account and handles permission rejection.
