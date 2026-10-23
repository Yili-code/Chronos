# Followup timing extraction

- Updated: 2026-10-23T16:48:06+08:00.
- Base 278d6e7; branch codex/event-followup-timing.
- Deployment live gate revealed a second loss: focused EventDetails extraction received only the latest message, even when the classifier combined the original event and answer. The initial PR mock tests did not exercise this provider fallback.
- Scope: pass combined instruction, current message and clarification context to focused event timing extraction. Context is untrusted and unrelated new events must not borrow timing.
- Implemented: provider payload fix and HTTP provider regression tests for combined and absent instruction.
- Validation: 1,495 Python tests passed (56.63s), one existing warning. Documentation 85 links / 59 files / zero missing; diff check passed. Live production-provider configuration on this working source passed the two-turn exam 09:20-11:00 scenario and an unrelated agenda request. No task/Calendar/Telegram writes. Production readback confirms chronos-exam-3892735 remains at 100%; candidate 278d6e7 has zero traffic. CI pending.
- Commit/push: pending; no production data writes or Telegram test messages.
