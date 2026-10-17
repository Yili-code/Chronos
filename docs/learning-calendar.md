# Approval-gated learning calendar

Chronos can propose a study block after a task is created, write it to Google
Calendar only after the owner presses **Allow**, and ask for progress after the
event ends. The five-minute deadline scheduler also drives this workflow, so a
missed webhook tick recovers without leaving the computer on.

## Owner rules

- Study blocks start at or after 09:00 and end at or before 21:00, Asia/Taipei.
- Every day, 12:00–13:00 and 17:00–18:00 allow only tasks of at most
  10 minutes. Longer blocks cannot overlap either window. Automatic scheduling,
  random alternatives, manual edits and approval rechecks enforce this rule;
  existing Calendar events remain unchanged.
- AI estimates remaining active work from the task title/project and completed-session
  evidence. Proposals show total minutes, confidence and assumptions; they do not
  imply access to course material. The structured estimate accepts 10–1440 minutes,
  rounds session length up to five minutes, and caps each proposed session at 180.
  Larger estimates explicitly indicate remaining work, without automatically
  creating multiple bookings. Ten-minute manual blocks are supported for all tasks.
- Owner-chosen durations and the course-scope rule take priority over AI. Estimates
  are cached with the task snapshot across retries and requeues; task changes cause
  a fresh estimate. Invalid/unavailable AI responses fall back to completed-session
  averages (at least three reports) or 90 minutes, visibly labelled. No retry loop
  is added to each scheduler tick; another replan can try estimation again.
- Course-scope checks, using the same English/Chinese classifier as the task-list
  grouping, default to 10 minutes. This rule takes priority over learned averages
  and legacy queued default estimates. An explicit owner edit overrides it and
  survives rescheduling or postponement. Scope checks accept 10–180 minutes;
  other manual blocks also accept a 10-minute minimum. Their quick duration choices
  add five minutes or double the duration (10 becomes 15 or 20 minutes).
- Fixed course hours plus reserved/completed study blocks total at most 540
  minutes per day, on weekdays and weekends. Completing a task does not reset
  that day's budget. Nonstudy personal events block availability but do not count
  as study hours.
- Respect the task deadline and check Google Calendar plus the configured course
  timetable. A persisted `learning:course-calendar-policy` controls the teaching
  dates and excluded exam/holiday dates. Without it, slots are conservatively
  blocked. Google availability does not replace the timetable.
- Each proposal displays MM/DD (weekday), start/end time and duration. No calendar write
  occurs without an owner callback bound to that exact proposal card.
- Check availability again on approval. Changed tasks, expired proposals or new
  conflicts require another proposal and another approval.
- If no block fits, report it. Never relax the cap or move the deadline silently.

## Authorization and deployment

ChatGPT's Calendar connection is separate from the cloud bot. Enable
`calendar-json.googleapis.com` in the OAuth client project. Run the local helper
with a Desktop OAuth client; the cloud option reads the already provisioned Gmail
client from Secret Manager without printing it:

```powershell
.venv\Scripts\python.exe -m chronos.calendar_authorize --cloud-project YOUR_PROJECT --gcloud-account YOUR_DEPLOY_ACCOUNT --account YOUR_CALENDAR_ACCOUNT
```

The owner must approve `calendar.events` and `calendar.readonly` in Google's
browser consent screen. The loopback callback uses a random state and PKCE,
validates the selected primary calendar account, and atomically saves separate
Calendar credentials into ignored `.env`. Gmail credentials are preserved.
The temporary consent-link file is ignored by Git and excluded from cloud source.
Revoked grants or short-lived grants from an OAuth app in Testing may require
reconnection; successful local authorization is not proof of cloud activation.

After the code has been deployed, verify and publish the grant:

```powershell
.venv\Scripts\python.exe -m chronos.calendar_deploy --project YOUR_PROJECT --account YOUR_DEPLOY_ACCOUNT
```

The publisher verifies the account, discovers writable personal/shared calendars
for conflict checks, stores credentials as separate pinned Secret Manager versions,
grants the runtime account access, and enables the feature on Cloud Run. It does
not create a test event. Existing service settings and secrets are retained.
Ordinary source deployment preserves this feature's settings and secret bindings.
Check `/ready` for the deployed release and `/plan` for connection/workflow status.
Also verify the existing `chronos-deadline-reminders` Scheduler job is enabled
and targets `/internal/deadlines` every five minutes.

Only tasks created after the activation timestamp receive automatic proposals.
Use `/plan <task-id>` to request a proposal for an existing task. `/plan` by itself
is read-only. Proposal preparation may wait until 09:00 during quiet hours.

## One proposal at a time

The owner sees one current study proposal. New requests join a queue without
reserving a time or daily budget. Edit keeps the same task active. Only a
successful, reconciled booking after Allow releases the turn and immediately
prepares the next proposal against the updated Calendar and daily budget.
Retries, uncertain writes and unavailable slots keep the current turn. Scheduled
session check-ins run independently, even while another proposal waits for Allow.

The durable `learning:proposal-queue` turn is claimed transactionally. On upgrade,
an existing active turn is retained; otherwise an in-flight booking or the task
currently being edited takes priority. Other legacy unapproved proposals return
to the queue with their chosen durations and deadlines preserved. Their tokens
are invalidated and Telegram keyboards removed, with durable cleanup retries.
Old callbacks cannot approve or edit a queued task out of turn.

**Postpone** on a proposal or its Edit menu means waiting for external conditions.
It leaves the task open and its deadline unchanged, releases the current approval
turn, and shows the next ready task. The task list and `/plan` show its waiting
status. It receives no automatic study proposals until the owner explicitly uses
`/plan <task-id>` to resume. Deadline reminders still apply. Chosen duration is
preserved. Manual edit context and old buttons are invalidated transactionally;
Postpone cannot alter an already approved Calendar event.

## Editing a proposal

Press **Edit** to see four choices. **A bit longer** adds 30 minutes;
**Much longer** doubles the current duration. Each block is capped at 180 minutes,
and the menu shows the resulting duration. The original start is retained when
it fits; otherwise another available slot is proposed. **Random other time**
chooses among valid 15-minute boundaries within the deadline and seven-day
horizon, excluding the current interval. It preserves the duration. No suitable
alternative leaves the menu active and reports the constraint.
Unapproved proposals are movable reservations rather than confirmed events.
An owner edit may reclaim their time or daily budget. Conflicting proposals and
additional proposals needed to respect the daily cap are atomically
queued for replanning, retaining their chosen durations and deadlines. Their old
Allow buttons cannot book the replaced time. Confirmed/in-flight bookings, classes
and actual Calendar conflicts remain fixed. If an approval starts during the
availability check, the edit aborts and asks the owner to retry.
**Choose manually** opens a reply prompt for `10/20 15:00 90` (date, start time,
duration in minutes). ISO dates also work. Invalid or conflicting times leave the
proposal in editing mode. The replacement card has a new token; the previous
Allow button cannot book it. Press **Allow** on the replacement to book.
Old cards recover the latest proposal or Edit menu instead of leaving the owner
at a dead end. An old Allow or duration/time choice only refreshes the view;
it never approves or modifies the replacement. Proposal and Edit-menu message
IDs are bound separately so returning to Edit on the original card still works.
`/planedit <task-id> MM/DD HH:MM minutes` also revises a pending proposal.

Manual entry keeps a chat-level session for two hours, refreshed after invalid
input. Follow-up text does not need Telegram's reply arrow. `/cancel` returns to
the edit menu; explicit slash commands and replies to unrelated cards retain
their normal routing. Successful edits clear the session. Date/time-only text
outside a session is never treated as a new task.
Weekday periods such as `星期日早上` or `tomorrow afternoon` search within that
period, keeping the current duration unless minutes are supplied. Morning is
09:00–12:00, afternoon 12:00–18:00, and evening 18:00–21:00, still constrained by
the configured study hours. Conflicts or insufficient continuous time leave the
session active. Deadlines remain unchanged and are named in rejection messages.
At the 180-minute block limit, longer-duration buttons are omitted.

The 115-1 course import starts 2026-10-19 and repeats weekly through 2026-12-18.
The final regular day is inferred from the official final exam week 12/21–12/25.
Midterm week 10/26–10/30 is excluded; final exams are beyond the recurrence end.
Source [NTOU official calendar](https://academic.ntou.edu.tw/p/405-1005-123146,c834.php?Lang=zh-tw).
Existing personal recurring events are preserved. Teacher-specific exam changes
require a timetable update. The policy expires with the teaching period and does
not imply next semester has the same courses.

## Follow-up and learning

Every day at 21:00 Asia/Taipei, the cloud job `chronos-evening-agenda` calls
`/internal/agenda/tomorrow` with the scheduler secret. It sends the following
local day's Google Calendar agenda to the owner on Telegram, including weekdays,
times and duration, or an explicit empty-day message. Calendar reads expand
recurring events and cover the primary plus configured conflict calendars;
cancelled/declined events are omitted. This reminder does not write events.
Per-day, per-part receipts prevent duplicate deliveries. Read failures retry;
ambiguous Telegram delivery requires inspection rather than a blind resend.
The computer can be off. The local scheduler also supports the same 21:00 job.

Check-in cards combine the planned start and duration on one line, separate sections
with blank lines, and use concise English with one concrete actual-duration example.
The result buttons remain the primary path; detailed outcome syntax is omitted
from the card to keep the check-in short.

At the end of a booked event, the next daytime tick sends one check-in. It reads
the Calendar event again to honor a changed end time; deleted/cancelled events
are not recreated. Follow-up is deferred outside 09:00–21:00.

- **Done as planned** records the planned duration and completes the task.
- **Need more time** leaves the task open with unknown actual duration.
  It queues another proposal, which still needs Allow before any calendar write.
- **Did not start** leaves the task open and records zero study minutes.
- `/studyfeedback <task-id> <minutes> done|partial|skipped [reason]` records actual
  time and optional interruption context. Use `/plan <task-id>` for another block;
  it still needs approval. Feedback is transactionally deduplicated.

Completed sessions and feedback are retained as evidence. After at least three
completed reports in the same task project, future proposals use a bounded,
15-minute-rounded moving average of reported duration. This is a simple time
estimate, not a claim of improved academic outcomes. Reasons are retained for
inspection; they do not automatically change code, hard scheduling limits or
deadlines. Per-session reasons and aggregate timing are separate from AI usage.

## Failure and side-effect contracts

Calendar event IDs are deterministic per approved proposal. After an ambiguous
insert, reconcile that same ID before retrying; verify private ownership markers.
No broad calendar update/delete operations are exposed. Existing external events
remain intact, and task edits do not silently move approved Calendar events.
Completing a task early stops later prompts but leaves its existing event intact.
Approved blocks conservatively retain their original daily budget even if an
event is later removed; a cancellation does not silently increase the daily cap.

Proposal delivery uses a durable claim. An ambiguous Telegram send is not blindly
repeated; `/plan` exposes the uncertain delivery state. An owner can explicitly
request another proposal when no event is booked. A tick lease prevents concurrent
schedulers from proposing overlapping blocks. Calendar errors fail closed; task
creation remains successful and proposal preparation can recover on a later tick.

Tests exercise the approval gate, class and Calendar conflicts, quiet hours,
daily budget, invalid owner/card, changed tasks, deterministic event reconciliation,
follow-up, edited event end times, and feedback persistence against both SQLite
and the transaction-checking Firestore fake. Real Calendar consent and a real
approved block/check-in must be verified separately before calling the entire
integration live-verified.

Natural schedule edits also accept remaining-work statements such as
`待會花一小時應該就可以看得完（已完成部分）`. AI extracts 60 minutes,
requests the nearest available slot for “soon”, and preserves the progress note.
A duration-only edit keeps the current start; an explicit date/time uses that
start. All results pass the existing deterministic deadline, class, Calendar,
meal-window and daily-budget checks. Ambiguous/unsupported edits and AI errors
leave the proposal unchanged and the edit session open. Partial progress never
marks a task complete; every revised proposal needs a fresh Allow.

During an unapproved proposal edit, an explicit standalone `已完成`, `完成了`,
`done` or `completed` completes that current task and retires its proposal/menu.
The transaction checks the active queue turn, token and task snapshot, clears the
manual-edit context and releases the next task. It never creates Calendar events
or infers actual work minutes from planned duration. `已完成部分` is not completion.

Proposal cards place date, weekday, time range and session minutes on one line
without When/Duration labels or repeated approval text. A blank line separates
that timing line from the AI estimate. Allow, Edit and Postpone remain unchanged;
removing the sentence does not change the approval requirement.

All Chronos-authored Telegram messages must be in English, including completion
acknowledgements, edit prompts, errors and AI-generated progress notes. Chinese
owner input remains supported. Keep wording concise.

## Single-session late study exception

The normal cutoff remains 21:00. If a manual time or requested evening window
would require a later session, ask Yes/No with its exact date and time first.
Yes creates a revised proposal bound to that exact interval; Allow is still
required to book it. No preserves the old proposal. Confirmation expires after
15 minutes and is invalidated by a task change. Later edits require confirmation
again. Search never passes local midnight and all deadlines, meal windows, class,
Calendar and daily workload checks remain enforced. Automatic scheduling keeps
the normal cutoff; no global setting is relaxed.

## On-demand tomorrow agenda

`/tomorrow` reads the full next day (00:00–24:00, Asia/Taipei) live from all
configured agenda calendars. All-day events appear first, followed by timed
events in chronological order with time range and duration. Blank lines separate
each event; long agendas split into readable messages. The command is read-only,
shows an explicit empty day or retry message, and does not consume the 21:00
reminder's delivery receipt. Pending proposals are not Calendar events.

## Next free two hours

`/free` reads Calendar availability and searches the next seven days for the
first uninterrupted two-hour opening within 09:00–21:00. It excludes classes,
Calendar busy intervals and daily meal windows 12:00–13:00 and 17:00–18:00.
The search starts now, rounded up to the next minute, and jumps past conflicts.
It only reports availability, without booking events or modifying study budgets,
proposals or reminders. Unapproved proposals do not reserve calendar time.

The result is a single line, e.g. `10/20 (Tue) · 09:00–11:00`.
Reply explicitly to that slot with an event description to preview a personal
Calendar event for the entire two-hour interval. AI converts the title to English;
requests to change time are rejected. Allow rechecks availability before inserting
one private event; Cancel writes nothing. No study task or study budget is created.
Slot replies expire after two hours or at the slot start, whichever comes first.
A newer reply supersedes the older preview for that slot. Deterministic event IDs
reconcile ambiguous responses and repeated clicks without duplicate inserts.

After a personal-event Allow succeeds and its confirmation is delivered, delete
the matching preview card. Keep failed or uncertain previews available for retry.
Cleanup retries reuse the saved update receipt without repeating the booking or
success message; a missing preview is treated as already cleaned up.
