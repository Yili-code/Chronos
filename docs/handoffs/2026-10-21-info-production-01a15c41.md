# Publish info command

- Updated: 2026-10-21T10:37:39.000098+08:00 (Asia/Taipei).
- Branch/base: main / 85e8734; hook reports clean/current. User authorized production integration.
- Scope: deploy committed source and register owner Telegram menu; no application edits.
- Preflight: yili-chronos-prod / asia-east1 / chronos currently routes 100% to chronos-00162-ht6. Service template release e874041; study/calendar enabled, Gmail/internal scheduler disabled. Existing historical tagged revisions retained.
- Decision: use clean Git archive helper and source deploy with only release SHA/label updates, preserving current environment/secrets and scheduler configuration. Deploy without traffic, verify tagged candidate then promote. Generic bootstrap script would reset environment/secrets and reprovision unrelated resources.
- Validation: source change previously passed 136 targeted tests and docs checks.
- Status: complete. Deployed release 85e873436d37a10d869be466173ad87a7fe21033 as chronos-00247-bud; candidate /ready matched release before promotion. Production traffic readback confirms 100% to this revision.
- Live evidence: production /live=live; /ready=ready with matching release; authenticated /internal/status reports readiness ready and Telegram webhook configured. Telegram getWebhookInfo matches production /telegram/webhook. Owner menu setMyCommands followed by getMyCommands exact readback succeeded: info present, help absent. No synthetic Telegram messages or task mutations were sent.
- Next: no remaining deployment step for this request; user can invoke /info. Rollback reference: prior traffic revision chronos-00162-ht6.
- Commit/push: application release already pushed; this completed deployment record is prepared for commit/push, publication verified through Git tracking state. No canceled implementation.

- Completed verification: 2026-10-21T10:40:33.424284+08:00 (Asia/Taipei).
