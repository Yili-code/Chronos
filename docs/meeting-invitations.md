# Reviewed meeting invitations

Implementation and mock tests do not establish production deployment, real invitation delivery or recipient agreement.

Start with a reviewed candidate from `協調會面：日期、時間、時區、長度`. Before creating a private hold, prepare an external invitation with `準備會面邀請 <candidate-code>：alice@example.org,bob@example.org；地點：台北辦公室`. Explicit email addresses are required; no AI-selected attendee list or same-name contact inference. Existing private holds are not silently converted in this phase.

The private preview displays every attendee, title, start/end/timezone and location. Confirm with `確認會面邀請 <invite-code>` within fifteen minutes. Preparation is local. Confirmation rechecks the source thread, account/calendar binding, contact preferences and configured-calendar availability; a durable claim precedes the provider write. Guest calendars remain unverified. The invitation uses a deterministic event ID, immutable reviewed fields and `sendUpdates=all`. Guests cannot edit/invite others, and their guest list is hidden by default. Calendar acceptance is not proof of email delivery.

`查看會面邀請` or `查看會面狀態` reads the event and verifies its identity, ownership, exact attendees and content. An organizer event status of confirmed does not establish attendee acceptance. Every requested attendee must be present and accepted, with organizer status confirmed, before all-accepted is reported. Declined, tentative, missing or omitted RSVP information remains declined/pending/unverified. This is a point-in-time observation, not a guarantee against later changes.

`取消會面邀請準備 <code>` abandons a local invite/cancellation review. It does not recall an operation already started. For a submitted invitation, use `準備取消會面邀請 <candidate-code>` and review its event/guest list, then `確認取消會面邀請 <cancel-code>`. Cancellation rechecks the owned event and uses its ETag, with notifications to all invited guests. Only the provider's expected cancellation acknowledgement establishes cancellation. Changed guests/content are not silently cancelled.

Unknown create/delete outcomes are not replayed. Read-only reconciliation can recover a matching created event. An unresolved cancellation requires further reconciliation; it is never reset to an automatic retry. Definite API rejections require fresh review. No tests send to real contacts or modify real calendars.

Remaining coordination work: conversion of existing private holds, rescheduling, automatic RSVP polling/owner updates, negotiation based on reply content, and conference-link creation. This phase does not claim these capabilities.

Official contracts: [events.insert](https://developers.google.com/workspace/calendar/api/v3/reference/events/insert), [event resource and attendee response status](https://developers.google.com/workspace/calendar/api/v3/reference/events).
