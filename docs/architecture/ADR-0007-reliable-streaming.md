# ADR-0007: Reliable Run-bound Streaming

- Status: Accepted
- Date: 2026-09-02

## Context

Phase-00 forwarded provider deltas directly. A disconnected client could not discover the run,
resume output, or distinguish completion from transport loss. Process memory could not provide
durable replay.

## Decision

Every stream belongs to one persistent `AgentRun`. Events are written before delivery with a
monotonic per-run integer sequence protected by a run-row lock and database unique constraint.
The protocol contains:

- `run.started`;
- `response.delta`;
- `response.completed`;
- `run.failed`;
- `run.cancelled`;
- `run.interrupted`.

Every terminal run has one terminal event. SSE uses the durable sequence as `id`. Clients resume
with `GET /api/v1/runs/{run_id}/stream` and `Last-Event-ID`; the server returns only later events,
then tails the active run. `X-Run-ID` is returned when streaming begins. Persist-before-send may
redeliver an event when a network acknowledgement is lost, so consumers use event IDs; this is
at-least-once delivery, not an exactly-once claim.

Retention is finite. Cleanup deletes expired events only for terminal runs. Active-run events
are preserved, and pruning is an explicit maintenance operation in Phase-01.

## Consequences

- Disconnect/reconnect can continue without gaps within retention.
- Clients must store the last processed event ID and de-duplicate by `(run_id, sequence)`.
- PostgreSQL write volume increases with every streamed delta.
- Resume is unavailable after retained events are pruned; a future API may expose a more
  explicit expiration marker.
