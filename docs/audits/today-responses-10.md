# Today-list replies: sequential audit

Synthetic date: 2026-10-24, 17:30 Asia/Taipei. Display: ISPM 18:00–19:00, Codeforces 19:05–21:05. ISPM is task 2; global task 1 is an unrelated report. Codeforces is a Calendar event without a linked task. Each case starts from a fresh database. SQLite and Firestore fake must pass before moving to the next case. Webhook, receipts, task mutation and scheduling code are real; Telegram and Calendar transports are mocked. Live Gemini outputs on synthetic data are captured for reproducible replay.

| Case | User reply | Acceptance | Sequential result |
|---|---|---|---|
| 1 | Study ISPM Chapter 2 做完了 | Complete only linked ISPM; unrelated task untouched; redelivery deduplicated | First failed: course-progress fallback. Reply binding fixed; both stores passed. Related system/agenda regression: 94 passed. |
| 2 | 第一個提前到今天 17:55，維持一小時; follow-up 第一個改到今天 18:05，維持一小時 | Resolve displayed row 1 to task 2. Preserve protected-time policy: reject 17:55; accept 18:05–19:05 without duplicate insert or touching the adjacent contest | First failed: model picked global task 1. Scope/ordinal context fixed. Discovered pre-existing 17:00–18:00 short-task-only policy; original expectation of accepting 17:55 was a test-design error, not a product defect. Kept that rejection and added legal-time substep. Both stores passed. Existing natural-schedule regressions + case 1: 32 passed. |
| 3 | 往後挪一點 | Ask which event and what time/window; no changes | Initially appeared safe because it asked about time, but final acceptance review found the model implicitly selected ISPM. Added deterministic missing-target clarification; final both-store replay passed. |
| 4 | 第一個改到 | Ask for missing time; do not guess | Live output clarified correctly. Initial harness assertion depended on specific question words; replaced with schema/side-effect acceptance and replayed both stores successfully. No product correction for this case. |
| 5 | 取消第二個 Codeforces Round 1129 | Explain unsupported standalone-event cancellation; no substitute task or partial change | First live output incorrectly said the displayed event could not be found. Added explicit standalone-event limitation; both stores passed. Cancellation itself remains unsupported. |
| 6 | asdf ### 🧩 ??? | Request clarification or reject; no writes | Live output asked what help was needed; no product defect. Corrected an overly narrow wording assertion to schema/no-side-effect acceptance; both-store replay passed. |
| 7 | 第一個做完了 (next local day) | Expire the old today list and request refresh before interpretation | First failed: completed task from expired list. Added local-date guard; both stores passed before case 8. |
| 8 | 第一個做完了 (task renamed after list) | Reject stale snapshot and require refresh | First failed: completed renamed task. Added task/plan snapshots, pre/post-interpretation and pre-transaction checks; both stores passed before case 9. |
| 9 | 這兩個都標記完成 | No partial completion of mixed task/event batch; explain limitation/clarify | First failed: completed ISPM only. Added single-target gate for agenda batch mutations; both stores passed before case 10. |
| 10 | 第三個提前到今天 18:15 | No third displayed row; reject before model; never select hidden global task | Passed both stores, using the ordinal-range gate added for case 2. No model request required. |

The cases are sequential, not ten live samples on an unchanged baseline. Outputs are bounded samples of provider behavior, not a general model-accuracy guarantee. No production task/event writes or Telegram sends. Deployment is outside this audit authorization.

## Final acceptance review

The initial acceptance assertions missed the implicit target in case 3 and checked the data change, but not the success reply, in case 2. These were corrected explicitly rather than treating the first green run as sufficient. The stronger case-2 check reproduced a success-reply defect introduced by the new snapshot check: a completed booked edit was mistaken for a concurrent stale-list update. Booked edits retain their existing ETag/snapshot checks and now return their actual result; task mutations retain transaction-time agenda checks. All ten cases were then rerun in order, stopping on any failure; each passed on SQLite and Firestore fake on the final implementation.

Supporting regressions cover exact per-message pagination/order (including equal starts), out-of-scope model task IDs, a task changing during interpretation or before commit, and `/today` refresh sent as a reply. Every user case checks that the hidden task remains open, no extra task/event is created, and a duplicate Telegram update does not send a second reply.

## Evidence and limits

- `tests/fixtures/today_responses/1.json`, `2.json`, `4.json` and `6.json` hold validated live-provider outputs replayed on the final route. Case 2 includes two classification/edit calls for protected and legal starts.
- Samples `3.json`, `5.json`, `7.json`, `8.json` and `9.json` preserve the earlier live interpretations that exposed weak or unsafe behavior. The final deterministic guards block these inputs before model invocation. Case 10 never needed a model call.
- Successful completion means completing the linked task. It does not delete a Calendar event or invent progress feedback. Standalone Calendar event edits/cancellations and mixed multi-event mutations are explicitly unsupported here.
- This audit concerns Telegram replies to newly generated `/today` messages. Existing unbound messages are not retroactively associated; ordinary messages without a Telegram reply retain the global conversational flow.
- Firestore fake and transport mocks do not prove production Firestore contention, live Calendar writes, Telegram rendering, or live end-to-end delivery. The bound snapshot catches local task/plan changes; externally edited Calendar events are subject to the existing booked-edit ETag checks, not a new continuous synchronization guarantee.
- Final-tree validation: 1,773 Python tests passed in 110.72s (one existing Starlette deprecation warning); 21 extension tests passed; documentation links (87 links/95 Markdown files), compilation and whitespace checks passed. CI pending publication. No deployment performed.
