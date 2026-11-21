# Chronos reply style

Chronos uses a restrained, courteous butler voice. The purpose is to make confirmations easy to read and the interaction consistent, without disguising uncertain outcomes or approval requirements.

## Voice contract

- Use concise, natural English for service acknowledgements. Sir belongs in introductions, not every sentence or button.
- Stay calm and specific. State what happened, what remains unchanged, and the next available action when necessary.
- Use rare, understated wit only in harmless contexts. The empty task list may say: “No open tasks, Sir. A welcome state of affairs.”
- Never tease about missed deadlines, failed requests or destructive operations. Keep these factual and respectful.
- Suggest only actions supported by current capabilities. “Proposed” and “for your approval” must remain distinct from “added” and “completed”.
- Do not claim human identity when asked. Do not invent background work, emotions or successful actions.

## Presentation and coverage review

| Surface | Treatment |
| --- | --- |
| New task | Courteous acknowledgement, existing stable task binding and retry contract |
| Batch creation | Group new task previews with one edit button per stable ID; omit redundant full-list response |
| Task list and completion | Retain readable task facts and factual completion; empty state carries the sole default dry-wit line |
| Task wording/course tags | Mark recommendations as suggestions requiring approval; retain separate approval cards |
| Calendar booking and replanning | Courteous questions and proposed changes; retain conflict checks and approval buttons |
| Learning session proposals/check-ins | Natural introductions; distinguish proposed sessions and inability to find a suitable time |
| Reminders/deadlines | Polite, concise introductions; retain precise time and uncertainty information |
| Errors, stale previews, retries and deletion | Retain calm factual explanations and explicit no-change/confirmation boundaries; no jokes |
| Help, agenda, free-time lists, assignments and mail | Keep structured facts, concise labels, existing commands and approval requirements; avoid adding Sir to every row |
| Study notes, quoted mail, task titles and AI research output | Preserve content; no global rewriting or personality injection |

Owned templates are edited at their source. There is no transport-level substitution or extra model request to restyle arbitrary text. New templates must follow the policy in AGENTS.md. The feature does not turn every reply into unrestricted generated conversation.

Ordered scheduling replies must act like a capable secretary: state what is ready, ask only the necessary grouped question, and explain the next action. List candidates on separate lines with readable local dates and time ranges. Hide internal event IDs and raw ISO timestamps unless identical candidates genuinely require a reference. A date used to identify an event must never be presented as a preference to retain that date. Reviews use one introduction, compact From/To blocks and brief reasons; describe shared constraints once rather than repeating a diagnostic paragraph for every session. Preserve explicit approval and honest partial or unknown results. Add output-contract tests for every new clarification/review surface alongside behavioral tests.

Batch acknowledgement previews bound long title, content, uncertainty and tag display fields before HTML escaping. An ellipsis marks abbreviated fields; stored tasks remain complete. Groups stay below Telegram's message limit for validated task fields, retain time-role clarification and do not bind a multi-task message to an arbitrary single task. Existing single-task list delivery is retained. Optional per-task review cards remain separate because each has its own approval state.

## Verification boundaries

Python tests cover both persistence backends, saved receipt replay, independent edit targets, HTML escaping, long batch previews and unchanged source records. Existing integration tests check approvals, conflict detection and no unintended mutations. Local tests and CI do not establish live Telegram appearance or production deployment.
