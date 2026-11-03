# Meeting candidates and private holds

This workflow checks the owner's configured calendars and manages a private tentative hold. It does not claim that another attendee is free, an invitation was delivered, a conference link exists, or a meeting was confirmed.

Reply to the intended person's Gmail card with `協調會面：明天下午三點，台北，30 分鐘` or `plan meeting: tomorrow at 15:00 Asia/Taipei for 30 minutes`. Chronos verifies the Calendar owner, reads the available mail thread and extracts one candidate. Missing/contradictory constraints require clarification. Incomplete message evidence and do-not-contact preferences block candidate creation.

The candidate displays the target address, title, offset-aware time, IANA timezone and duration. It checks the owner's availability but does not query or invent another person's availability. Ambiguous DST overlaps require an explicit UTC offset. Supported duration is 15–240 minutes, with a future start within one year.

## Explicit private reservation

`暫留會面時段 <code>` confirms the displayed candidate for a personal calendar hold. Before writing, Chronos reloads the source thread, rejects changes and rechecks availability. Candidates expire after an hour. Concurrent operations claim the same version transactionally.

The event uses a deterministic ID, `status=tentative`, private visibility and no attendees or conference data. The existing Calendar transport uses `sendUpdates=none`. A private hold is not a meeting invitation or attendee agreement.

If Calendar creation is uncertain, Chronos persists that state and does not retry automatically. `查看會面狀態` reads the deterministic event ID and accepts it only when its ownership marker, version, title, time and lack of attendees match. New mail invalidates an unchanged-source approval before hold creation; it is not automatically treated as attendee consent or permission to delete an existing hold.

## Cancellation

`取消會面暫留 <code>` cancels the local candidate or explicitly deletes the matching private hold. A native deletion first verifies ownership, unchanged content, no attendees and an ETag, then uses the provider's version precondition. Changed or repurposed events are not deleted. Cancellation sends no attendee notification. Unknown deletion results require read reconciliation; they are not automatically replayed.

Preparing a local cancellation does not grant permission to overwrite a later in-flight creation. State/version guards keep these operations separate. Native API writes are outside database receipt transactions; claims and local state are compatible with both SQLite and Firestore.

## Remaining boundaries

Multi-party negotiation, other attendees' availability, invitation delivery and external RSVP tracking remain outside this private-hold workflow. Meeting-related email can be reviewed through the separate reply-draft workflow, but saving a native Gmail draft is still not sending it. Existing baseline acceptance cases for these broader capabilities remain unresolved until implemented and tested.

Regression tests include changed source cancellation, newly occupied slots, uncertain/incomplete writes, no-attendee event payloads, DST ambiguity, opt-out, external event changes, ETags and simultaneous operations. Ten additional real webhook integration tests verify interactions between drafts, tracking, contacts, preferences and private calendar holds using synthetic external transports.

Provider contracts: [Events insert](https://developers.google.com/workspace/calendar/api/v3/reference/events/insert), [Events resource](https://developers.google.com/workspace/calendar/api/v3/reference/events).
