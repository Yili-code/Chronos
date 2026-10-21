# Natural language entry recommendation

- Updated: 2026-10-21 10:47 +08:00 (Asia/Taipei).
- Branch/base: main / bc583c8. Startup hook reports current history; working tree verified clean. Reviewed PROJECT_STATUS, AGENTS and recent routing/info handoffs; no active overlapping implementation identified.
- Scope: product and routing analysis only; no application implementation or deployment requested.
- Evidence: inspected prepare_message and Telegram replan routing in chronos/main.py and COMMANDS/menu merging in chronos/telegram_menu.py. Unbound ordinary text generally falls through to ai.parse and task creation; calendar questions have a narrow regex path, replan requires its command or existing interaction context. Current list command is /tasks, not /task. Menu registration merges old commands, so reducing visible entries requires explicit pruning.
- Recommendation (not yet an accepted requirement): natural language as the primary entry, a small shortcut menu, and retained legacy command handlers. Route validated structured intents to shared service actions; do not execute arbitrary model-generated commands. Resolve reply/session context first, use stable task IDs, clarify ambiguous targets, preserve preview/confirmation for calendar moves and destructive bulk actions. Failed classification must not fall back to creating a task.
- Suggested rollout: first query/create/edit/complete/undo/replan intents, then remaining specialized operations. Keep explicit shortcuts usable during AI failure. Acceptance should cover intent distinctions, ambiguous references, stale context, AI failure and duplicate updates.
- Verification: read-only source review; no tests or live services invoked. No application behavior changed. No abandoned implementation.
- Next: implement only after user requests implementation; decide /task alias versus existing /tasks and full specialized-operation scope.
- Commit/push: only this analysis record prepared for commit and push; publication result to be verified via Git tracking state.
