# ADR-0004: Persistent Session and AgentRun State

- Status: Accepted
- Date: 2026-09-02

## Context

Phase-00 kept conversation history in one process. It could not preserve sessions through a
restart, expose an execution failure after the request ended, or distinguish conversation state
from provider work. Persisting only messages would also make pending, failed, cancelled, and
ambiguous executions invisible.

## Decision

PostgreSQL is the production persistence system. `RuntimeStore` is the runtime-facing contract;
SQLAlchemy and database models remain inside its adapter. The schema separates:

- `agent_sessions`: conversation identity, owner metadata, and version;
- `agent_runs`: request identity, idempotency, state, attempt count, outcome, and worker owner;
- `agent_messages`: stable per-session sequence and committed transcript;
- `agent_stream_events`: stable per-run sequence and replay payload.

Conversation commit semantics are success-only and atomic. The current user message and final
assistant message are inserted in the same transaction that moves the run to `completed` and
persists `response.completed`. Failed, cancelled, and interrupted runs contribute no committed
messages. Existing Phase-00 messages are assigned deterministic sequence numbers during the
upgrade.

Applications never call `Base.metadata.create_all()`. Alembic owns upgrade and downgrade.
Development/tests may explicitly select the in-memory contract implementation; production must
select PostgreSQL and provide a URL.

## Consequences

- Sessions and successful context survive restart.
- Run failures remain queryable without contaminating later provider history.
- Success involves one larger transaction and row lock, favoring correctness over maximum QPS.
- Database availability is now a production readiness dependency.
- Provider-side completion that is lost before commit remains ambiguous and is not replayed.
