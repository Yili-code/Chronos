# Chronos Engineering Operations - Learning Guide

This guide extracts the reusable engineering ideas behind the Chronos hardening work. It is not a feature inventory. The goal is to help you make better product and architecture decisions when a small personal tool becomes a real service.

## 1. Operational truth matters more than a green process

### Why you need this

A process can be running while the product is unusable. The database may be unreachable, a webhook may point to an old revision, or a scheduler may be paused. If every condition is represented by one `ok`, deployment becomes theatre rather than evidence.

The reusable model is:

- **Liveness:** Is the process alive?
- **Readiness:** Can this revision serve its required workload now?
- **Integration status:** Are external boundaries configured and connected?
- **Business evidence:** Did the intended user-visible outcome actually occur?

This distinction gives you more precise incident reasoning. A successful HTTP response proves only that one boundary responded; it does not prove a Telegram message arrived or that a task mutation was correct.

Reusable sentence: **A healthy process is necessary, but it is not sufficient evidence of a healthy product.**

## 2. Fail closed at trust boundaries

### Why you need this

Configuration absence is not the same as authorization. Treating an empty password or webhook secret as "authentication disabled" may be convenient locally, but it becomes dangerous when the same container is exposed publicly.

Chronos now distinguishes local SQLite mode from public mode. Public mode requires the owner chat, Web password, webhook secret, scheduler secret, and Telegram token. Missing security configuration prevents readiness instead of silently weakening protection.

Use this decision rule:

1. Identify whether a boundary can mutate valuable state.
2. Define the credentials and ownership evidence it requires.
3. Reject startup or readiness when those invariants are absent.
4. Make insecure local operation explicit and narrow.

This is broader than security. It prevents ambiguous operating modes, which are a major source of production mistakes.

Reusable pattern: **Missing configuration must not broaden authority.**

## 3. Build releases from immutable input

### Why you need this

If deployment packages the working directory, untracked scripts and local experiments can enter the build even when they were never reviewed. You also lose the ability to answer a basic question: "Which exact source produced this revision?"

The stronger release chain is:

`commit -> clean archive -> build -> revision label -> readiness readback`

Each arrow should be verifiable. The deployed service exposes its release SHA, and the deployment script compares it with the commit it archived. CI generates commit-scoped evidence rather than maintaining a single mutable test report.

This improves debugging, rollback, auditability, and founder-level decision making. You can separate code quality from deployment quality instead of arguing from memory.

Reusable sentence: **The artifact under test must be the artifact you ship.**

## 4. Prefer branch-by-abstraction over rewrites

### Why you need this

Chronos grew from a Telegram task bot into a system with Web, Gmail, Study, Firestore, PDF and scheduler paths. Large modules are a signal that boundaries need clarification, but a rewrite would discard working transaction and idempotency behavior.

The safer sequence is:

1. Define a narrow protocol around the behavior you already rely on.
2. Make both implementations satisfy the same contract.
3. Add contract tests.
4. Move one route or service boundary at a time.
5. Keep behavior stable throughout the migration.

This technique lets architecture evolve without creating a second unfinished system. It is especially useful in startups, where reliability and speed matter more than architectural novelty.

Reusable pattern: **Create a seam, prove parity, then move behavior across it.**

## 5. Measure cost before optimizing it

### Why you need this

Retries, model failover and scheduled polling can increase provider usage, but intuition is a poor cost model. Premature limits may damage the user experience while solving a problem that does not exist.

Chronos records aggregate AI requests, provider attempts, statuses and provider-reported token totals. It does not store prompts, responses, credentials or task text. This creates a decision surface without expanding the privacy risk.

Ask these questions before optimizing:

- Which operation causes the requests?
- How many logical requests become multiple provider attempts?
- Does the provider report actual tokens, or do we only have an estimate?
- Is the cost recurring, bursty or caused by failures?
- Would caching or a cheaper model preserve the required quality?

Reusable sentence: **Instrument first; optimize the measured bottleneck.**

## 6. UX includes failure recovery

### Why you need this

A polished happy path is not enough. Users need to know what happened, whether state changed, and what action is safe next. Chronos already applies this principle to Telegram delivery receipts and saved edits; the Web companion now also has loading, error and retry states, and it uses the configured timezone.

A good failure message answers:

1. What failed?
2. Did the system change state?
3. Is retry safe?
4. What should the user do next?

This is both UX and distributed-systems design. If delivery outcome is unknown, blind retry can create duplicates. Honest uncertainty is more useful than false confidence.

Reusable pattern: **State the outcome, preserve the invariant, offer the next safe action.**

## 7. Evidence has levels

### Why you need this

Engineering teams often mix test evidence, deployment evidence and user-outcome evidence. That creates inflated claims and weak decisions.

Use four levels:

1. **Static evidence:** syntax, schema and configuration checks.
2. **Local behavioral evidence:** deterministic tests and isolated integration tests.
3. **Deployment evidence:** revision, traffic, readiness, environment and scheduler readback.
4. **Live outcome evidence:** the real external action or user-visible result.

For this change, local evidence includes the full Python suite passing, 48 passing extension tests, a successful isolated demo, valid documentation links, valid PowerShell syntax and a verified dependency lock. Production and Telegram attachment delivery must be reported separately after they actually succeed.

Reusable sentence: **Name the evidence level before stating the conclusion.**

## 8. A practical review checklist

Before releasing a stateful automation, ask:

- Does public mode fail closed?
- Are liveness and readiness different signals?
- Can every deployed revision be traced to one commit?
- Are retries idempotent, bounded and honest about unknown outcomes?
- Do storage implementations share a tested contract?
- Is user-visible time derived from configuration?
- Are cost decisions based on aggregate measurements?
- Does the validation record distinguish local, deployed and live evidence?
- Are unrelated working-tree changes excluded from the commit?
- Can the final artifact be verified at the actual delivery boundary?

The deeper principle is simple: **Make important system properties executable, observable and reviewable.** That is how a small tool becomes dependable without becoming unnecessarily complex.
