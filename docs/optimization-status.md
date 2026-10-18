# Optimization review — 2026-10-18

Implemented: isolate reminder failures; hide expired confirmation previews; identify interrupted sends; stop retrying permanent Telegram 4xx errors; respect 429 retry_after; avoid idle reminder writes; condition delivery completion on claim ownership; run readiness database calls outside the async event loop; report aggregate reminder problems through authenticated /internal/status; exclude all local release artifacts; compile sources in CI.

Existing safeguards confirmed: locked dependencies, Python version matrix, extension tests, commit-scoped CI evidence, shared repository contracts, backend-parametrized reminder tests, and assignment scan last-success state. These were already implemented and should not be duplicated.

Remaining, not implemented: reminder data retention and migration away from a single document; broader async database offloading; production query-count and latency measurement; last-success telemetry for every scheduled job; real Firestore integration coverage for all transaction types; broad module decomposition; lint/type-check adoption. These need further design or measurement. No claims of completion or performance gains are made for these items. Runtime capacity is unchanged without load evidence.

Retention must preserve deduplication identifiers and uncertain delivery records. No historical records were deleted. Large-file size alone is not evidence that a broad rewrite improves runtime reliability. No real Telegram failures were intentionally injected into production.
