# ADR-0006: Cancellation and Retry Budget

- Status: Accepted
- Date: 2026-09-02

## Context

Changing a database status does not stop an active model call. Layered SDK, provider, and runtime
retries can multiply calls, latency, and token spend. Completion and cancellation also race.

## Decision

The Supervisor owns each provider invocation in a process-local `asyncio.Task`. Cancellation
first performs an atomic active-to-cancelled transition and writes `run.cancelled`; it then
cancels and awaits the local task. A row lock arbitrates cancellation against completion, and
terminal states are immutable. A worker that does not own the live task returns
`CANCELLATION_UNAVAILABLE` rather than pretending that execution stopped.

The application Runtime is the only retry owner. The OpenAI client is constructed with
`max_retries=0`; the installed Agents SDK runner's opt-in retry settings remain unset. Every
attempt is persisted before the call.

Retries require an explicit retryable classification and remain within both maximum attempts
and maximum elapsed time. Backoff is capped exponential delay with optional bounded jitter.
Timeout, connection, rate-limit, and provider-server failures may retry. Invalid request,
authentication, permission, guardrail, and tool-policy failures do not. Streaming never retries
after the first persisted delta.

Unlimited retry is prohibited because it creates unbounded latency, spend, and cancellation
uncertainty. Process shutdown marks owned active runs `interrupted`, cancels tasks, and never
replays ambiguous work automatically.

## Consequences

- Cancellation stops cooperative SDK/network awaits in the owning worker.
- Completion/cancellation races produce exactly one terminal state and event.
- Cross-worker cancellation needs a future distributed execution owner or queue.
- Failed provider attempts may consume tokens the SDK cannot report; this limitation is
  documented rather than estimated.
