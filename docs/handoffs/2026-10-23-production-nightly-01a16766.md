# Production nightly planning release

- Updated: 2026-10-23T14:59:28.130135+08:00.
- Branch: codex/production-nightly-release; base/release 9f4098039676c4b70de599df9e37f885815d9302 in isolated worktree.
- Authorization: user explicitly requested production deployment and selected latest main (tomorrow-aware variable-duration planning), replacing the earlier fixed-four behavior.
- CI: merged-SHA run 39914654313 verified success for Python 3.11, Python 3.12 and extension.
- Before: production chronos-00254-geg, release e10a74f68fe65601a7e009964535c6cf347dce88, 100% traffic. Project yili-chronos-prod, region asia-east1. Calendar/Study enabled, Gmail disabled. No nightly scheduler job exists; existing evening agenda runs at 21:00 Taipei.
- Plan: archive exact commit; deploy candidate without traffic while preserving existing settings and secrets; verify candidate readiness/release; promote 100%; provision and verify authenticated daily nightly job. No manual proposal generation or Calendar booking is authorized by deployment alone.
- In progress; no deployment completed yet.

- Updated: 2026-10-23T15:02:22.371267+08:00. Completed production rollout from exact Git archive of 9f4098039676c4b70de599df9e37f885815d9302; candidate deployed without traffic first, then promoted after checks.
- Verified candidate and production `/ready`: ready/public/Firestore, exact release SHA. Revision chronos-nightly-9f40980 serves 100% traffic. Temporary verification tag removed.
- Verified live `/internal/learning/status`: Calendar owner/read available, approval_required=true, work hours 09:00–21:00, default 90 minutes, combined daily class/study budget 540 minutes.
- Verified environment preservation against immutable former revision: only CHRONOS_RELEASE_SHA changed; all existing environment values/secret references, service account, concurrency, timeout and resource limits preserved. No secrets rotated or printed. Existing Gmail=false and Calendar/Study=true retained.
- Created chronos-nightly-planning Cloud Scheduler job: ENABLED, `0 21 * * *`, Asia/Taipei, POST `/internal/learning/nightly`, three retries, 3600-second retry duration, 240-second deadline; scheduler credential verified against existing agenda job without disclosing it.
- Live endpoint checks: no scheduler credential returns 403; authenticated call outside 21:00 returns waiting_for=21:00 Asia/Taipei. This checks routing/authentication/hour gating without generating a proposal or writing Calendar events. Existing evening agenda job is preserved.
- Limits: tonight's actual Gemini whole-day allocation and Telegram proposal delivery have not yet occurred; no full nightly generation was manually triggered. Calendar event creation remains owner-approved only.
- Implementation/deployment complete; publish this evidence through a documentation-only PR. Runtime stays on the exact code SHA above; later documentation commits do not imply redeployment.

- Final readback: scheduler next execution 2026-10-23T13:00:00Z = 2026-10-23 21:00 Asia/Taipei. Production traffic and exact release were rechecked after verification-tag removal. Documentation check: 60 links in 45 Markdown files, no missing targets; diff check passed.
