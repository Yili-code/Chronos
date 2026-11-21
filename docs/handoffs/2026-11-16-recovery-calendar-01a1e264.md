# Recovery scheduling with a reviewed Calendar overlap

- Updated 2026-11-16 11:36 Asia/Taipei. Branch `codex/recovery-explicit-calendar`, isolated worktree, base `98b34c1` fetched from main. Other local work preserved.
- Scope includes a weekday-period hint on the unstarted-task card, lookup of the referenced course occurrence, a concrete single-use conflict review, and optional Calendar location. Persistent preferences are outside this first release.
- The booking is claimed only after explicit confirmation. The scheduler rechecks event identities, versions, times, availability, and task details before inserting a private event. The original events are preserved. Unknown busy time prevents booking.
- Local validation: 4,664 Python tests passed, including SQLite and Firestore recovery coverage. Compilation, diff inspection, and the documentation check passed. No live Calendar or Telegram writes were made.
- Publication: commit and push this isolated change, verify the PR's Python 3.11, Python 3.12, and extension jobs, then merge. Deployment is separate and has not been requested.
