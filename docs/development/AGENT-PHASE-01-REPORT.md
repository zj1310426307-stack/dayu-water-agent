# AGENT-PHASE-01 Development Report

## 1. Executive Summary

Dayu Water Agent 0.2.0 now provides a production-grade single-Supervisor runtime foundation:
durable PostgreSQL state, idempotent execution, fail-fast session concurrency, true task
cancellation, bounded retry ownership, resumable run-bound SSE, crash reconciliation,
dependency-aware readiness, structured correlation, internal metrics, and optional OpenTelemetry.
All core Phase-01 Gates A–N passed. No domain agent or Dayu Tiangong integration was added.

## 2. Baseline

Development started from clean Phase-00 commit
`57f402b0bd8d24175767b1ca97f2a66e4a5634f3`. Its 43 tests passed before modification. Work was
isolated on `phase/agent-phase-01-runtime`; no merge, rebase, force push, tag, or release was made.

## 3. Architecture Changes

The runtime now depends on provider-neutral `contracts` and `RuntimeStore`, not SQLAlchemy or the
OpenAI SDK. The Jellyfish boundary discipline led to moving shared session/message values into
`dayu_agent.contracts`, with compatibility re-exports for Phase-00 callers. One Supervisor still
owns all workflow behavior; PostgreSQL, providers, FastAPI, and CLI remain adapters.

## 4. Persistent Session

`SQLAlchemyRuntimeStore` implements session creation/read/history/clear plus run/event operations.
Production configuration requires `SESSION_STORE=postgres` and a psycopg PostgreSQL URL. Memory
storage remains explicit for tests and offline development. There is no fallback on database
failure.

## 5. AgentRun State Machine

Runs use `pending`, `running`, `completed`, `failed`, `cancelled`, and `interrupted`. Only declared
transitions are valid and all terminal states are immutable. Runs preserve request, trace,
session, provider/model, attempt, owner, timestamps, usage, safe error code, result, and metadata.

## 6. Idempotency

An optional global `Idempotency-Key` binds to canonical message/session/user/metadata semantics.
Same key and digest returns the original run; changed semantics returns HTTP 409. A unique
constraint handles cross-process races. A discovered lock-wait race was fixed by checking the key
again after acquiring the session lock, with a real PostgreSQL regression test.

## 7. Concurrency

Same-session concurrency is fail-fast. Reservation locks the session and the database partial
unique index independently prevents multiple pending/running rows. There is no global execution
lock, so different sessions run concurrently. A 50-session real PostgreSQL API test passes.

## 8. Cancellation

Each owned run is an `asyncio.Task`. Cancellation first commits the terminal state/event, then
cancels and awaits that exact task. Row locking arbitrates completion versus cancellation.
Cross-worker cancellation fails explicitly instead of only changing a database flag while work
continues.

## 9. Retry Budget

The Supervisor owns the sole budget: attempt cap, total elapsed cap, capped exponential backoff,
and optional jitter. Retry classification is explicit. The OpenAI client is constructed with
`max_retries=0`, while Agents SDK opt-in retry settings are unset. Every attempt is persisted and
instrumented before the provider call. Streaming cannot retry after output begins.

## 10. Reliable Streaming

Every stream event belongs to a run and is stored before delivery with a monotonic sequence.
`X-Run-ID`, SSE `id`, and `Last-Event-ID` support reconnect/replay. The system promises durable
at-least-once delivery within retention, not exactly-once network delivery.

## 11. Crash Recovery

Startup reconciles active runs owned by prior worker identities to `interrupted` and appends a
terminal event. Ambiguous provider completion is never automatically replayed. Shutdown stops
admission, marks local active work interrupted, cancels tasks, closes the store, and flushes
optional telemetry.

## 12. Observability

JSON logs and `TraceContext` now carry request, run, session, trace, provider, model, status,
duration, and attempt data. A dependency-free metrics contract covers run totals/failures/
cancellations, provider attempts/retries, duration, active runs, and stream resumes. Optional OTEL
spans cover HTTP request, agent run, session load/commit, provider run/attempt, and stream persist.
Missing optional packages or a collector do not block default startup.

## 13. Database and Migrations

Alembic `0002` upgrades Phase-00 tables, deterministically backfills message sequences, and adds
runs/events plus foreign keys, unique order constraints, and the active-run partial index. Upgrade,
full downgrade, and re-upgrade passed on PostgreSQL 17.11. Runtime startup never calls
`create_all()`.

## 14. API Changes

Phase-00 endpoints remain. Chat accepts `Idempotency-Key`; results include run/trace IDs. New
endpoints query runs, cancel runs, and replay/tail streams. `/ready` distinguishes runtime, store,
and provider; `/health` remains liveness-only. The generated OpenAPI JSON is committed and checked
in CI. Request fields and declared body size are bounded; all errors retain one safe shape.

## 15. Security Review

No model-accessible database, shell, Python, arbitrary filesystem, or control tool was added.
SQL uses parameterized SQLAlchemy expressions. Secrets and database URLs are excluded from safe
settings/log responses. Provider/SDK causes remain internal. Retry, stream retention, input size,
and tool duration are bounded. Dangerous permission defaults remain fail-closed.

## 16. Test Coverage

Final result: 74 passed, one opt-in OpenAI live test skipped, 86.34% branch-aware coverage. Tests
cover transitions, atomic commit/no-pollution, idempotency/conflict/races, same/different session
concurrency, cancellation/backoff/races, retry classification/exhaustion, event sequence/resume,
crash reconciliation, database outage behavior, API contracts, CLI compatibility, security, and
real PostgreSQL persistence.

## 17. Verification

Ruff, strict mypy, OpenAPI synchronization, pytest, coverage threshold, pip check, compileall,
Alembic up/down/up, Compose config/build/up, health/readiness, SSE replay, API restart persistence,
non-root process identity, and PostgreSQL migration version all passed. Full evidence is in
`AGENT-PHASE-01-VALIDATION.md`.

## 18. Performance Baseline

The correctness-oriented 50-session PostgreSQL/API concurrency test completed in 3.99 seconds on
the local Windows/Docker host. This includes HTTP processing and database work and is not a
capacity or SLA claim. No Redis, broker, queue, or large monitoring stack was introduced.

## 19. Known Limitations

- No knowledge base, flood evaluation, report, hydraulic, GIS, or Dayu Tiangong agent.
- No enterprise IAM, distributed queue, cross-region HA, or automatic device control.
- Cancellation is process-owner-local; a different live worker cannot cancel the provider task.
- A provider may complete remotely before a crash prevents the local success commit; ambiguous
  work is interrupted and not replayed.
- Stream replay is limited by finite retention and is at-least-once at the network boundary.
- One session supports one active run and has no built-in queue.
- SDKs may not expose usage for failed attempts, so token totals can undercount such work.
- OpenAI live behavior is not verified without explicit opt-in and credentials.

## 20. Deferred Work

Distributed execution ownership, queues, IAM/rate limits, usage/cost accounting improvements,
retention scheduling/operations, HA, and domain capabilities remain deferred. Redis, Kafka,
RabbitMQ, LangGraph, PostGIS business queries, and an in-house DAG engine were intentionally not
introduced.

## 21. Recommended Phase-02

Proceed to Tool Runtime & Permissions only after preserving the Phase-01 storage/run contracts.
Phase-02 should strengthen tool authorization, confirmation, audit, cancellation, and resource
limits without bypassing success-only conversation commits or exposing infrastructure to models.

## Defects corrected during Phase-01

| Root cause | Fix | Regression coverage |
|---|---|---|
| Phase-00 persisted the user message before provider/output success | Success-only atomic pair commit owned by RuntimeStore | Provider failure and output guardrail tests require empty history |
| PostgreSQL same-key waiter could see an active run after its pre-lock key lookup | Recheck idempotency after the session lock | Real PostgreSQL concurrent same-key test |
| Windows psycopg async mode rejects the Proactor event loop | Test/CLI PostgreSQL paths select the compatible Selector loop | Real PostgreSQL suite on Windows |
| Provider retry ownership was implicit in SDK defaults | OpenAI client retries disabled; Runtime owns one explicit budget | Client max-retry, attempt, exhaustion, and non-retry tests |
