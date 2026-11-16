# Split an unschedulable work session

- Started 2026-11-16 Asia/Taipei from `1f984c5` on `codex/split-work-sessions`. Add an explicit split option to the no-slot proposal. Preserve the original task, planning window, deadline, and total duration.
- Reuse scheduling constraints and the existing deterministic multi-session booking and reconciliation flow. No production write requested. Validation and publication pending.
- Preview uses the original search boundaries, minimum 15-minute segments and unchanged requested total. Confirmation repeats availability and checks task and daily load before reserving. The existing booking flow confirms each deterministic Calendar ID and retains approved partial work for retry. The original task advances through its booked segments.
- Local verification passed: 8 new targeted cases and 4,683 full Python tests, documentation links and diff checks. The tests cover 60 + 120 minutes, insufficient capacity, new conflicts and an uncertain insert across SQLite and Firestore. No live Calendar writes; publish via PR after local verification.
