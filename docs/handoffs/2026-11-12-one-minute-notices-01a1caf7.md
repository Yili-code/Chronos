# One-minute notice expiry

- Started 2026-11-12 Asia/Taipei on codex/one-minute-notices at 5033099. Scope: the course progress reminder and the missing-time email event prompt in the screenshot. Keep the original course prompt, other mail questions, and the seven-second default behavior.
- Existing expiry accepted only seven seconds. Pass explicit durations through both delivery paths and retain the established persisted expiry mechanism. No production release authorization for this change.
- Validated 58 focused tests, 4,622 full Python tests, 21 extension tests, static and documentation checks. Existing send and deletion APIs mocked. The original study question and unrelated mail prompts retain their previous lifetimes. Next: PR checks and merge.
