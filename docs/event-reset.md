# Rebuild events by type

Use `/eventreset <type> [constraints]`. The category is required; an empty command or `/eventreset help` shows usage and makes no changes. It never defaults to every category.

Examples:

- `/eventreset study`
- `/eventreset meeting 只排未來`
- `/eventreset workout 明天下午`
- `/eventreset work 包含過期未完成的行程`

Built-in event labels include exam, contest, meeting, social and other. Custom labels such as workout or work are also accepted as a single word, with hyphens/underscores allowed. Chinese aliases include 學習, 會議 and 健身. This is Chronos's activity category, not Google's special Calendar `eventType` field. Explicit category metadata takes precedence; untyped/other events and open tasks can be classified from their meaning, with a question when unclear.

The planner selects only the requested category, including unbooked unfinished tasks and independently created Calendar events. Other categories remain in place as busy commitments. Preconditions can refer to a selected unit or to an unselected event as a fixed time anchor. Descriptions, locations, colors, visibility, reminders and custom event properties are preserved when standalone or existing task sessions are recreated. No fake tasks are created for standalone events. Existing booking references are updated to the replacement event ID.

Study retains its established chapter/deadline rules and authorized compression; `/studyreset` remains an alias entry point. Non-study events preserve their original durations and are not split. Booked non-study task segments preserve their durations and details; additional explicit task effort can receive additional segments. Unbooked tasks use their explicit effort or a validated estimate. If the unchanged durations cannot fit, no old events are deleted and the owner is asked to adjust the scope. Non-study planning defaults to configured daytime hours and may use explicitly supplied hours, while tasks retain workload/deadline checks. Courses, other bookings and pending reservations remain protected.

The new schedule defaults to the next 30 days. The planner reads the surrounding 30 past/future days for untyped candidates and separately discovers explicitly tagged category events, with bounded pagination. Too many candidates require a narrower scope. `只排未來` bounds tagged discovery to the future planning window. Expired events are never assumed unfinished/completed automatically: if selected historical events have no explicit scope, the service asks whether to include them. A short answer retains the original category. `/cancel` cancels a pending scope question; an applying/partially completed operation must be recovered under its original category.

Shared, recurring, foreign-owned, all-day, currently running, non-whole-minute events or events with attachments/conference data cannot be silently deleted/recreated. Missing/uncertain task or booking links stop the operation. The existing durable executor saves the complete feasible plan before deletion, checks ETags and fresh constraints, reconciles deterministic replacement IDs after unknown outcomes, and pauses the background planner during incomplete operations. This is not an atomic transaction across Calendar and the database; partial progress is reported and retained. Long receipts paginate and retry only pages not recorded delivered.

Tests use isolated stores and Calendar/Telegram substitutes. They do not establish universal model classification accuracy or live Calendar deletion/recreation correctness.
