# Gemini primary and secondary API keys

Keep the existing primary key and optionally configure a secondary key from a
separately provisioned Google Cloud project:

```dotenv
CHRONOS_GEMINI_API_KEY=your-primary-key
CHRONOS_GEMINI_API_KEY_SECONDARY=your-secondary-key
CHRONOS_GEMINI_KEY_COOLDOWN_SECONDS=60
```

Store real values only in local `.env` or Secret Manager. Never commit keys.
Both keys use the same configured model and endpoint, so the secondary project
must allow that model. Different keys in the same project share provider quota;
this feature cannot detect whether two different keys belong to the same project.
An identical key in both fields is deduplicated. Either field alone is sufficient.

## Runtime behavior

- The primary is preferred whenever it is available.
- HTTP 429, 500/502/503/504, 401/403, and an explicit `API_KEY_INVALID` rejection
  allow failover. Ordinary request errors such as 400/404/422 do not.
- With two distinct keys configured, one operation makes at most one attempt per
  available key. There is no switch-back loop within that operation.
- With only one key, task AI retains its existing three-attempt transient retry
  limit. HTTP 429 is never immediately retried against the same key.
- A failed key is skipped for the configured cooldown. For 429, `Retry-After`
  and Google's structured `RetryInfo.retryDelay` can extend the cooldown, up to
  48 hours. If no usable retry information is supplied, the configured cooldown
  applies. After the cooldown, the next request tries the primary again.
- If both keys are cooling down, the operation fails immediately without making
  another provider request. Existing task edit retry/error handling remains in use.
- Task requests reserve part of their existing timeout for the secondary, so a
  slow primary cannot consume the entire timeout before failover.
- Invalid model output is reported as an error, not retried with another key.
- Logs identify primary/secondary and HTTP status only, never the key or provider
  error body. Key fields are excluded from the settings object's representation.

Cooldowns are in-memory and local to each provider instance/process, keyed by
credential fingerprint and model endpoint. They reset on process restart and are
not a distributed quota manager or a guarantee against concurrent in-flight
requests. Provider quotas and billing remain authoritative.

## Covered features and persistence

Task creation/editing, course-day interpretation, progress summaries and mail
summaries use `ExternalAI` and therefore share its routing. Study PDF summaries
and assignment drafting use the same routing policy for explicit HTTP failures.
Study deliberately retains its no-inline-retry behavior for uncertain transport
failures/timeouts; those continue through the existing job recovery workflow.

Every actual Study provider attempt, including a secondary attempt and the review
pass, must reserve from the existing daily Study budget. Failover does not bypass
that limit. Task AI has no new project-level spend cap; configure provider-side
quotas/billing controls separately.

Failover occurs before task persistence. It does not rerun Telegram commands or
create an additional task. Telegram update deduplication and edit conflict checks
continue to apply. Network retries may still incur provider processing costs even
when the first response was lost.

## Cloud Run deployment

`scripts/deploy_cloud_run.ps1` reads `CHRONOS_GEMINI_API_KEY_SECONDARY` from the
environment or `.env`. When supplied, it stores the value as
`chronos-gemini-api-key-secondary` in the deployment project's Secret Manager,
grants the runtime service account access, and binds that secret version to the
Cloud Run environment. The key can belong to a different project from the one
hosting the secret and service.

If the local secondary is omitted, the script preserves the service's existing
secondary secret reference. To disable it, remove the environment binding from
Cloud Run and clear the local value before the next deployment. Do not leave a
revoked key configured as a working backup.

For an existing deployment, configure the secret with the actual secondary key
and deploy the new code. Merely updating a local `.env` does not configure Cloud
Run. No API key or new Cloud project is created automatically by the failover
feature itself.
