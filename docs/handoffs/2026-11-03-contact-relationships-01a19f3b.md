# Local contacts and relationships

- Updated: 2026-11-03 Asia/Taipei. User-authorized continuation; LINE/Instagram deferred.
- Branch: codex/executive-assistant-audit, correspondence base 506afa6433218124104fe3136745583feb1ac29d. PR #124; correspondence CI must pass before pushing unrelated changes.
- Scope: owner-maintained local contact records, conservative identity/merge handling, optimistic versions, confirmed destructive operations, explicit interaction notes, birthdays/cadence and do-not-contact preferences. No Google Contacts integration or automatic outreach claimed.
- Files: chronos/contact_relationships.py, mail_workflow.py routing, main.py reply binding, new tests and documentation. Existing account-namespaced JSON persistence; no migration.
- Component tests: 32 passed on SQLite/Firestore fake before routing integration. New data comes only from explicit owner fields and the chosen mail-card sender; arbitrary email bodies are not imported as instructions or personal notes.
- Pending: wire handlers, webhook/transaction/race regression, full suite, CI and audit evidence. No commit/push/deployment yet for contacts.

## Implementation evidence — 2026-11-03 Asia/Taipei

- Wired explicit contact and relationship commands into owner mail-card actions, with bound Telegram previews. Missing card context asks for clarification instead of creating an unrelated task.
- Same-name people remain separate; duplicate email cannot overwrite/create silently. Updates require current version. Delete/merge require expiring tokens and unchanged versions. Merge refuses conflicts, preserves sources/history and keeps the stronger opt-out.
- Owner-sourced notes, birthdays and cadence support evidence-based manual suggestions only. No Google Contacts sync or automatic outreach. Registry limits include 700 KB serialized size; no history is silently dropped to meet capacity.
- Development repair: reset callback outcome flags on every transaction retry, avoiding a success claim from a discarded Firestore attempt. Preserve previous identity/preference values in bounded change history.
- Focused integration run: 101 passed for contacts plus mail correspondence/webhook compatibility. Later focused contacts/webhook run: 37 passed. Full suite running; result to be recorded before push.
- Prior correspondence commit 506afa6 CI run 41575347741 verified successful for Python 3.11, Python 3.12 and extension jobs.
- New meeting module is being developed separately and is not yet routed or included in the contact validation result. No production changes.
- Final contact-stage full suite: **3143 passed**, 1 existing Starlette/httpx deprecation warning, 190.30 seconds. Documentation: 101 links in 142 Markdown files, no missing targets; lint and diff checks passed. Ready for its own commit and PR CI; broader acceptance remains incomplete.
