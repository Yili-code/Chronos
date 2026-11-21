# Verified-source nightly allocation recovery deployment

- Updated: 2026-10-23T23:05:08.661185+08:00.
- Authorization: user explicitly selected deploy the combined version after PR #48 was completed. Review reply expiry is already live; this release adds the nightly allocation recovery fix.
- Source: 241d42bbfdc541e34c4c8501a0586e72018d0653 (PR #48). Exact-source push CI 39972016949 passed Python 3.11, Python 3.12 and extension, reverified before deployment.
- Baseline public release: 555bf651a0f8e44fffe2c546ebd0bfec80953a52. Preserve settings and secrets; zero-traffic candidate, verify exact release/readiness/configuration, guard unchanged production traffic, then promote.
- Scope: chronos / yili-chronos-prod / asia-east1. Main has since advanced with task-list grouping and deployment documents; those unrelated changes are excluded from this authorized exact-source deployment.
- Verification: runtime readiness, traffic, configuration and read-only affected-task recovery. No approve callback or Calendar insertion by this deployment procedure. Normal worker may generate a replacement approval card for existing invalid allocations.
- Status: starting release. Rollback revision recorded by the release helper. Implementation local full suite 1607 passed; relevant suites 89 passed; current exact-source CI passed.
- Next: complete candidate/promotion checks, verify current task state, publish sanitized handoff through documentation-only PR. No deployment for later documentation merge.
- Commit/push: implementation already merged; deployment record local pending publication.

- Completed: 2026-10-23T23:09:14.799511+08:00. chronos-nightly-replan-241d42b receives 100 percent traffic; release 241d42bbfdc541e34c4c8501a0586e72018d0653. Public readiness ready with Firestore; authenticated status ready, matching release, Telegram webhook/learning Calendar/Study configured, Gmail disabled.
- Candidate verified at zero traffic before guarded promotion. Environment and secret references unchanged except CHRONOS_RELEASE_SHA. Rollback revision chronos-review-expiry-555bf65. Existing tags retained.
- Existing-task check: legacy nightly no_slot was examined by the normal learning worker. It correctly became needs_replan because its title and project had changed after the saved snapshot. Estimated effort and deadline were unchanged. No replacement approval card or Calendar event was created; do not bypass snapshot protection. The user must request a fresh plan against the latest task state.
- Verification limits: runtime deployment and snapshot protection were observed live. Automatic alternative selection is proven by the regression suites, not a live synthetic booking or forced snapshot rewrite. No approve callback or Calendar insertion was issued by this procedure.
- Local sanitized evidence: .tmp/nightly-replan-release-241d42b.json; private affected-task readback .tmp/nightly-replan-affected-task-readback.json remains untracked.
- Next: publish this deployment evidence through a documentation-only PR; no application redeployment required for that record. All deployment actions complete.
