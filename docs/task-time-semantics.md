# Task times and editing

Tasks distinguish deadlines, planned work, and course/event dates. Telegram cards,
the daily list, and the Web dashboard display these roles separately.

| Input meaning | Stored value | Display |
| --- | --- | --- |
| Deadline with an explicit time | `due_at`, timezone-aware datetime | Due: 2026-10-10 17:00 |
| Deadline with only a date | `timing.due_date`, YYYY-MM-DD | Due: 2026-10-10 |
| Planned work time | `timing.scheduled`, date or timezone-aware datetime | Scheduled: … |
| Date identifying a lecture/event | `timing.event`, date or timezone-aware datetime | Course / event: … |
| Ambiguous expression | `timing.uncertain`, original wording | Time needs clarification: … |

No default 09:00 is assigned. Date-only deadlines do not create a timed reminder.
Deadline ordering includes date-only deadlines; course/event dates do not affect
deadline ordering. Existing stored timestamps remain unchanged because the system
cannot know which old timestamps were explicitly supplied.

The original time instruction is retained in `timing.source_text`. AI output is
validated before writing. Editing uses explicit `keep`, `clear`, and `set`
operations, preserving fields the instruction does not change. A simultaneous edit
that changes the task during parsing prevents the older result from overwriting it.

## Telegram editing

- `/edit 1 移除期限` uses the current list position, for compatibility.
- `/edit #42 移除期限` uses the permanent task ID shown on its Edit button.
- Reply to a Created or Updated task card with the desired change.
- `/tasks` provides Edit buttons for the first 50 tasks. Select one and reply to
  its prompt. The reply stays bound to the selected task even if the list reorders.
- A missing task number produces a usage hint and makes no changes.
- Completed/deleted task cards cannot create or modify another task.

Changing an already-sent Telegram message **does not replay commands or alter
tasks**. The bot explicitly explains this and asks for a new command or a reply
to a task card. This applies to both creation messages and commands, preventing
duplicate creation and accidental re-execution of `/done` or `/clear`.
Webhook update receipts deduplicate redeliveries of the same update.

## Screenshot regression

For a legacy task incorrectly stored with a Saturday 09:00 deadline, send:

```text
/edit 1 星期六是課程的時間非 due time
```

This correction works without an AI request. It clears the deadline, records
Saturday as a date-only course/event reference, and reports the before/after
values. The title, tag, and separately scheduled work remain intact. An ambiguous
new input such as `finish remote lecture in 星期六` is represented as uncertain
by the AI contract until the user clarifies the time role.

## Storage and rollout

SQLite initializes an additive `timing_json` column and a chat/message-to-task
mapping table. Firestore stores equivalent `timing` maps and message bindings;
legacy documents without timing remain readable. Both backends validate timing
values and use the same deadline ordering.

Restart the deployed service to load these changes. When `public_base_url` and
the Telegram token are configured, startup re-registers the webhook with
`message`, `edited_message`, and `callback_query`. Otherwise register the webhook
with those allowed update types using the existing deployment procedure.

Regression tests use mocked AI/Telegram transports and a Firestore transaction
fake that rejects reads after writes. They do not certify a live cloud rollout
or the accuracy of every possible natural-language expression.
