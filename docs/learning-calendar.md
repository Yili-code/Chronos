# Approval-gated learning calendar

Chronos can propose a study block after a task is created, write it to Google
Calendar only after the owner presses **Allow**, and ask for progress after the
event ends. The five-minute deadline scheduler also drives this workflow, so a
missed webhook tick recovers without leaving the computer on.

## Owner rules

- Study blocks start at or after 09:00 and end at or before 21:00, Asia/Taipei.
- New subjects default to 90 minutes per block.
- Fixed course hours plus reserved/completed study blocks total at most 540
  minutes per day, on weekdays and weekends. Completing a task does not reset
  that day's budget. Nonstudy personal events block availability but do not count
  as study hours.
- Respect the task deadline and check Google Calendar plus the configured course
  timetable. Course slots are conservatively blocked even on school holidays;
  Google availability does not replace the timetable.
- Each proposal displays MM/DD, start/end time and duration. No calendar write
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

## Follow-up and learning

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
