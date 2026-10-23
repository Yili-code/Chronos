# Calendar events and task content

Standalone personal events use a Calendar preview, followed by Allow or Cancel. They do not create tasks. A preview is not a booking: only a verified Calendar insertion returns “Added to Calendar”. A date, start and explicit duration/end are required; missing or inconsistent values prompt clarification. Current support covers future timed events of up to 24 hours, not recurring or all-day events.

```text
Calendar
├── Course                              blue   (9, existing course sync)
├── Assignment / study work session      green  (10, existing planning)
└── Personal event
    ├── Contest                         purple (3)
    ├── Meeting                         yellow (5)
    ├── Social / informal gathering     orange (6)
    └── Other                           gray   (8)
```

These are Chronos categories, not Google's eventType values. Each leaf uses a distinct legacy Calendar colorId. Existing /free previews retain orange. Existing Calendar entries and tasks are not automatically reclassified or migrated.

Example: `10/24 20:00 AtCoder contest 預計 3h` should preview the local date at 20:00–23:00, 180 minutes, contest category. The request does not become a completion-tracked task. Calendar identity, availability, preview expiry and message binding are checked before insertion. An explicit personal event is not restricted by study meal breaks. Retries reconcile a deterministic event ID, including a lost insertion response. The existing approval requirement remains.

Task title names the work; content records details or remaining work. `task 6 的 competitive programming 剩下一題程式題` means a content edit, with position resolved to a permanent task ID. `修改他的內容` can answer an active clarification without discarding the original target or detail. Explicit content editing cannot rename the title. Before and After labels use Telegram HTML bold.

For storage compatibility, content lives at `task.timing.content` in the existing validated JSON payload. It is detail metadata, not a date or scheduling constraint. Both SQLite and Firestore already persist and snapshot that payload; undo and edit conflict checks include it. Existing records omit content and require no migration. Cards and lists render it under Content. The model edit contract exposes an independent content operation, defaulting to keep for older responses.

Prompt examples improve semantic interpretation, but correctness also depends on the action allowlist, field guards, persisted clarification context, datetime validation and confirmed Calendar execution. Mock/provider-contract tests verify these boundaries; they do not measure live model classification accuracy or prove production delivery.
