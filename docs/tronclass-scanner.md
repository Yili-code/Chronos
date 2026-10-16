# TronClass scanner operations

The owner-only collector runs as a separate Cloud Run Job, even when the PC is off. It reads homework metadata; it never submits work, marks coursework read, downloads materials, generates drafts, or gives the public Telegram service school credentials.

## Scope and notifications

- Schedule: **daily at 06:00, Asia/Taipei**. The existing Scheduler resource retains its historical name `chronos-tronclass-weekly-scan`; do not create a second scheduler just to rename it.
- Course scope: the deployed `1151` semester and the seven IDs in `chronos/assignment_bridge.py::TRACKED_COURSES`. The semester must match exactly one `/api/my-semesters` record. Every returned course must belong to that semester. Upstream `is_active` previously referred to an older semester, so it is not used to silently change the configured scope.
- Only explicit `submitted: false` evidence is eligible. Submitted or unknown submission states are excluded. Expired first-seen work is durably baselined without creating a task; extending its deadline later does not create a surprise task.
- Eligible assignments produce a first-discovery card with **Open in TronClass** and **Edit task**. Later source changes refresh stored evidence without another discovery card. Owner deadlines remain authoritative.
- Timed deadline notices use the unified task reminder scheduler, independently of scanner discovery notices. See [usage](usage.md).
- A successful zero-new scan sends one status per date. The first identical failure is silent; the second consecutive identical failure sends one failure notice. Interrupted/uncertain Telegram deliveries are retained and never blindly resent.
- The scanner holds a ten-minute lease to prevent overlapping runs; the deployed task timeout is five minutes. Pagination is bounded to twenty pages of one hundred rows per endpoint; repeated pages or incomplete/invalid responses fail closed.

`/calendar` controls dates, not the semester or roster. Before a new semester, review the roster and configured semester together, validate them against source data, and publish a separate release.

## Release and verification

All Job scripts archive **committed HEAD** and choose the corresponding Dockerfile from that archive. Tracked changes must be committed first. Untracked local credentials, diagnostic output, and test databases cannot enter that archive. Job labels identify the source commit; scanner logs include the commit and semester.

```powershell
$env:CLOUDSDK_CORE_ACCOUNT = 'YOUR_AUTHENTICATED_GCP_ACCOUNT'
.\scripts\deploy_tronclass_assignment_scan.ps1
.\scripts\deploy_tronclass_assignment_scan.ps1 -EnableSchedule
.\scripts\deploy_tronclass_assignment_scan.ps1 -ExecuteNow
```

Deployment alone does not execute a scan. `-EnableSchedule` checks the actual schedule, timezone, target and enabled state; `-ExecuteNow` is an explicit scan with normal persistence and owner notifications. Preserve credential references and the collector service account when updating an existing Job. Never copy school secrets into the public service.

Verify these separately: source commit/image digest, Job configuration, execution completion, sanitized application outcome, persisted scan state, Telegram delivery, and Scheduler state. A successful Job process does not establish that the upstream scan succeeded: inspect the application outcome such as `completed`, `paused_by_calendar`, or `failure_recorded`.

## Read-only diagnostic tools

`scripts/deploy_tronclass_auth_probe.ps1` and `scripts/deploy_tronclass_assignment_discovery.ps1` deploy their tools without executing them. Add `-ExecuteNow` only for an intended authentication or discovery check. Reports exclude credentials, cookies, tickets, query strings and page text. The discovery tool uses the same semester and roster scope as the scanner.

The interactive local authentication wrapper is `scripts/spikes/tronclass_auth_probe.py`. `scripts/publish_tronclass_cas_credentials.ps1` is only for explicitly provisioning or rotating credentials; routine reconciliation does not require running it.

## Bounded rollback: preview, then confirm

The rollback tool has no baked-in historical window. Stop the faulty source of new tasks before considering a rollback. Preview an explicit timezone-aware window of at most one hour:

```powershell
.\scripts\rollback_bad_assignment_scan.ps1 -Start 'YYYY-MM-DDTHH:MM:SS+08:00' -End 'YYYY-MM-DDTHH:MM:SS+08:00'
```

Read the execution's sanitized preview: eligible count, skipped count and `plan_hash`. Preview performs no database or Telegram mutations. Applying requires a second invocation with the same window, `-Apply`, and `-ConfirmPlanHash` containing that exact hash.

Only open linked tasks created inside the window qualify, with at most 150 records. Changes to task, assignment, or delivery data invalidate confirmation. Firestore deletes and the message-cleanup outbox are committed atomically. Telegram deletion failures remain pending and can be resumed using the same confirmation. Completed tasks and scanner baseline state are preserved. This is a maintenance tool, not a Telegram command; no rollback is run as part of normal deployment.

## Local artifacts

Keep reusable source and tests in Git. Validation folders, one-off patch files and Job output logs are ignored and excluded from cloud uploads. Historical production evidence in `TEST_REPORT.md` remains a dated snapshot, not proof for a later release.
