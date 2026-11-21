# MIME evidence bounds and alternate renderings

- Updated: 2026-11-03 Asia/Taipei. Branch codex/executive-assistant-audit; base b3083fbae7cbdf29926e6757d3b108af0c9fd9a3.
- Scope: preserve materially different HTML/plain evidence throughout triage and AI context; bound MIME traversal/decode to fail conservatively on oversized structures. No attachment downloads or outgoing multipart implementation in this change.
- Planned files: gmail.py, mail_workflow.py, MIME regressions and capability documentation. No migration. No real-recipient writes.
- Validation, commit, CI and deployment pending. Recipient phase was pushed and is awaiting its independent CI.

## Implementation and local evidence

- Reproduced erroneous cleanup: promotional plain text plus an HTML-only payment deadline was classified disposable. The new test failed before the change.
- Decoder retains a different normalized HTML rendering in alternative_body, and propagates truncation. Triage conservatively keeps distinct renderings; summary/task/draft contexts retain both as untrusted data. Private cards disclose the difference.
- Traversal stops after 20 nested levels / 100 parts; combined encoded body budget is 131,072 characters. Overflow preserves the mail as incomplete. Attachments remain unread rather than silently approved.
- Focused regression run: 167 passed. Added further reply-context and hidden script/style checks before starting the full suite. Ruff, docs (152 Markdown files / 119 local links / zero missing), diff check and wheel/source build passed. Full-suite result pending.
- Recipient commit b3083fbae7cbdf29926e6757d3b108af0c9fd9a3 CI 41653154263 independently verified: Python 3.11 / 3.12 and extension all successful. No production deployment.

## Final local validation

- Full suite: 3,544 passed, zero failures/errors/skips; 142 existing warnings; 175.82 seconds. Final wheel production modules match source. Evidence: docs/evidence/mail-mime-2026-11-03.json.
- Preparing atomic commit/push to the existing PR. CI is pending for this MIME change. No deployment and no real-recipient writes.
