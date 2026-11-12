# Course hashtag production deployment

- Updated: 2026-10-23 Asia/Taipei.
- Authorization: user explicitly requested deployment.
- Baseline: chronos-followup-2b3efd3 at 100%, public readiness reports Firestore ready and release 2b3efd32852ed0e6a109eeb7e0a5c8efee67f207.
- Selected target: current main 12d8cc4b316056745a894d5acc41f5daee74eda3, containing PR #31 and existing production followup fix; only runtime delta is readable course tags. Later commits add deployment evidence only. Exact target CI must pass before deployment.
- Plan: clean archive, no-traffic candidate, preserve environment except release SHA, verify candidate readiness and internal status, check unchanged baseline traffic, promote, verify public release.
- Status: in progress; no deployment yet. No source changes, task/Calendar repairs or Telegram test messages planned.

- Exact target had no automatic merged push run; validation-only draft PR #35 uses current production as base and exact target SHA as head. No runtime edits. CI run 39930676935 queued/running. This PR is not intended for merging.

- Completed: 2026-10-23T17:20:23.739455+08:00
- Exact target CI 39930676935 passed Python 3.11, Python 3.12 and extension jobs. Candidate deployed from clean Git archive and verified before promotion.
- Production revision chronos-course-tags-12d8cc4 now receives 100% traffic. Public readiness: ready, Firestore, release 12d8cc4b316056745a894d5acc41f5daee74eda3. Authenticated status confirms same release, configured Telegram webhook, Calendar and Study; Gmail disabled.
- Compared baseline and candidate runtime environments: only CHRONOS_RELEASE_SHA differs. Baseline traffic unchanged before promotion; rollback chronos-followup-2b3efd3. Historical tags retained.
- Evidence: .tmp/course-tags-release-12d8cc4.json (local sanitized record). No production task/Calendar mutations or synthetic Telegram messages. Legacy card rendering covered by full feature tests and exact deployment CI, not a Telegram end-to-end delivery test. Existing delivered messages are not edited.
- Deployment complete; documentation-only publication pending.
