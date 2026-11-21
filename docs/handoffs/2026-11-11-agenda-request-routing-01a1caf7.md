# All-day agenda request routing

- Started 2026-11-11 Asia/Taipei from main 346d332 on codex/agenda-request-routing. User authorized fixing and deploying the agenda-routing issue. Screenshot text is regression input.
- Pending study feedback intercepts standalone messages, and its interruption detector misses the exact clear-tomorrow phrasing. The existing Calendar replan flow needs a literal full-day window for tomorrow and must retain approval before writes.
- Scope: learning reply, intent routing, Calendar preview and webhook regression. Main already includes PR 163 course-card expiry; disclose that dependency in the rollout.
- Implemented deterministic routing and tomorrow-to-next-midnight bounds, with explicit dates in the preview. An old Telegram message crossing midnight must name the date again. Existing movable/fixed event rules and Allow gate apply.
- Focused calendar and scheduling suites: 110 passed. Exact screenshot regression covers standalone and quoted replies, preserving the 180-minute reading draft, previews movable and fixed activities, verifies replay, and checks for zero Calendar writes before Allow. Static checks, 21 extension tests, and local docs passed. Full local Python suite: 4,616 passed with one existing deprecation warning.
