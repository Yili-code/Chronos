# Explicitly authorized reply sending

This builds on the reviewed single-recipient plain-text draft workflow. Creating a local proposal or saving a Gmail draft never sends it. Incoming email, AI output and OAuth scope do not grant permission to send.

## Owner confirmation

1. Review the local reply and explicitly save it as a Gmail draft using its current code.
2. Reply `準備寄出回覆草稿 <draft-code>` to the bound card/preview. Chronos verifies the account, reloads the source thread and reads the native draft. Known changes to the recipient, subject, parent message, thread or body block sending. Hidden CC/BCC, multiple recipients and attachments are rejected.
3. Review the displayed recipient, subject and full body, then reply `確認寄出回覆 <send-code>`. This independent confirmation expires after 15 minutes. The source and native snapshot are rechecked, and the send state is atomically claimed before the API call.

`取消寄送回覆` cancels a pending confirmation while retaining the native draft. It does not claim to recall a send that already started. A repeated receipt or repeated confirmation does not submit another send request.

The exact displayed plain-text message is supplied as an immutable MIME update in `drafts.send`. This prevents an unexpected recipient or body edited in another client during the final read/send gap from becoming the sent payload. Known native changes block the operation; the API does not lock the draft against concurrent editors, so a concurrent edit can be superseded by the specifically approved snapshot. Stop editing that native draft while confirming, or cancel and create a new reviewed version.

Recipient local-part casing is preserved; only the domain is normalized. The service never selects another contact from an AI-generated address. Current scope remains the one configured Gmail account, as selected by the user on 2026-11-03.

## Outcomes

States extend the draft workflow with `send_review`, `sending`, `sent` and `send_uncertain`. Only a valid provider response for the expected thread with a SENT label and valid message ID establishes `sent`. Acceptance by Gmail is not evidence of inbox delivery, reading or recipient agreement.

Definite authorization/validation/quota rejection returns to the saved draft and requires fresh owner confirmation. Timeouts, missing native draft after a send attempt, 5xx, malformed success or interrupted writes require Gmail reconciliation. They are never automatically replayed or represented as definitely unsent. A process interruption after the send claim also preserves the ambiguous state. There is no general send command that accepts an arbitrary address or arbitrary draft ID from a client.

## Validation boundaries

Automated tests use synthetic transports exclusively. They verify the actual MIME/API payload, hidden-recipient and native-edit rejection, account/card binding, source changes, expired/cancelled codes, concurrent confirmations, cancel-during-send, provider failure states and webhook receipt replay. No real contact received a test email.

Synthetic live Gemini checks validate the structured reply/candidate schemas separately; they do not send mail and do not establish real-mail factual accuracy. No production delivery claim follows from mock tests.

Official contracts: [drafts.get](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.drafts/get), [drafts.send](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.drafts/send), [updating content during draft send](https://developers.google.com/workspace/gmail/api/guides/drafts#send_drafts).
