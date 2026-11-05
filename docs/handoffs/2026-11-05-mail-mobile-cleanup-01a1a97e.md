# Mail acknowledgements and mobile links

- Updated: 2026-11-05 Asia/Taipei.
- Baseline: origin/main 90baa66; isolated branch codex/mail-mobile-cleanup and worktree. Fetch succeeded; unrelated original work preserved.
- Scope: seven-second terminal mail notices, concise consistent mail UI and noun titles, visible source links with account-aware URL generation, recovery card wording and existing action visibility.
- Findings: mail branch bypasses existing transient cleanup; source_text URLs are not rendered. Recovery actions already exist below screenshot crop; preserve behavior and stored reason keys.
- Status: implementation in progress; no commit/push/deployment. Deployment not authorized by this request.
- Validation pending: focused regressions, full Python suite, docs/diff, PR CI. Actual mobile Gmail app and Telegram delivery remain unverified.

- Follow-up authorization: user requested all second-screenshot improvements, tests, and direct deployment after verification. Preserve existing production settings and credential references; zero-traffic readiness/version checks and guarded promotion required.
- Implemented: consistent concise mail terminal notices; persisted deletion deadline and retry without resend; noun email titles and legacy render compatibility; account-aware browser URLs with RFC Message-ID search when available (Google documents rfc822msgid operator); visible task, schedule and feedback links; compact schedule/feedback titles and complete time ranges; neutral Chinese recovery card, preserved existing choices/reason storage; expired pending schedule notices transition directly to verified feedback; old schedule card cleanup only after successful feedback delivery with retry.
- Evidence so far: initial mail/recovery regressions 103 passed; new link/timer/retry regressions 22 passed. Initial default Python lacked dependencies, switched to existing .venv. Restricted test temp directory access required approved execution outside restriction. Larger scheduling regressions running.
- Caveats: Gmail does not provide a guaranteed public universal native-app message URL. Browser route retains account selection and exact Message-ID search where possible; native phone handling remains unverified. Existing legacy source IDs use thread/message fallback; no private production records rewritten.

- Final local validation: 4,207 Python tests passed in 221.73s; one pre-existing Starlette deprecation warning. Extension 21 passed; documentation 123 local links, zero missing; compilation and diff checks passed. New expiry/replay/cleanup/link/overdue-schedule regressions cover SQLite and strict Firestore where applicable.
- Mail scheduled-success cards also expire after seven seconds; persisted pending deletions resume on tick. Tasks and /plan adjustment entry remain available. This resolves accumulation without making progress cards disappear before a response.
- Publishing three atomic commits for mail action/link presentation, recovery copy, and schedule-card lifecycle. PR CI, merge-source CI, guarded deployment pending. Actual phone/native Gmail behavior and Telegram timing remain unobserved live.
