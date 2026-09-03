# ADR-0005: Idempotency and Session Concurrency

- Status: Accepted
- Date: 2026-09-02

## Context

Clients retry requests after timeouts and disconnects. Executing every retry can duplicate model
charges and conversation turns. Concurrent turns on one session can also read the same history
and commit in the wrong order.

## Decision

`Idempotency-Key` is optional on both chat entry points and globally unique within a runtime
store. It binds to a SHA-256 digest of canonical request semantics: trimmed message, explicit or
absent session ID, user ID, and sorted metadata. Request/trace IDs, timestamps, and transport
choice are excluded.

The same key and digest returns the original run and never invokes the provider again. The same
key with a different digest returns `IDEMPOTENCY_CONFLICT` (HTTP 409). The database unique
constraint resolves cross-process races.

One session may have one active (`pending` or `running`) run. Reservation locks the session row,
checks active state, and inserts the run in one transaction. PostgreSQL also enforces a partial
unique index on active statuses. A second request fails immediately with `SESSION_BUSY`; runs on
different sessions have no global lock.

Phase-01 does not add a session queue. Queue ownership, fairness, expiry, cancellation, and
backpressure would introduce a workflow subsystem before requirements justify it.

## Consequences

- Safe client retries share a stable run and provider invocation.
- Same-session order is explicit and database-enforced.
- Clients that want sequential turns must wait for a terminal state and retry after
  `SESSION_BUSY`.
- Global key scope requires clients to generate high-entropy, non-secret keys.
