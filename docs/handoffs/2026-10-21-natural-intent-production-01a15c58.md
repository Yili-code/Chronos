# Publish natural language entry

- Updated: 2026-10-21 11:20 +08:00 (Asia/Taipei).
- Branch/base: main / 2c04a71; startup hook reports clean/current. User explicitly authorized production deployment.
- Scope: deploy committed application and register the seven-entry owner Telegram menu. Includes a release-gating intent prompt correction discovered by live synthetic validation.
- Preflight verified: yili-chronos-prod / asia-east1 / chronos routes 100% to chronos-00247-bud, release 85e873436d37a10d869be466173ad87a7fe21033. Calendar enabled; Gmail disabled. Historical revision tags remain present.
- Decision: clean Git archive source deployment with only release SHA/label changes; preserve environment, secrets and scheduler configuration. Deploy without traffic, verify candidate readiness/release, then promote.
- Validation: implementation record contains 654 passing regression tests and 136 final focused passing tests; no live AI interpretation claim.
- Status: in progress. Initial candidate chronos-00249-jep (2c04a71) deployed with 0% traffic; not promoted. Live synthetic AI case for a reminder to submit a deliverable incorrectly returned reminder_create. Strengthened product-specific precedence and added explicit task-versus-notification examples in chronos/ai.py. Revalidation passed all seven synthetic cases: replan, agenda, task edit, task complete, deliverable creation, ambiguous target clarification and notification-only reminder. No real task or calendar mutation and no synthetic Telegram messages. 76 AI/intent tests passed after correction. Corrected candidate deploy, promotion and menu readback pending.
- Next: complete deployment and verification, then record results and commit/push this handoff.
- Commit/push: application already published; this deployment record pending. No abandoned approach.
