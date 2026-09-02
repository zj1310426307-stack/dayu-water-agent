# ADR-0001: Single-Supervisor Agent Runtime

- Status: Accepted
- Date: 2026-09-02
- Scope: AGENT-PHASE-00

## Context

Dayu Water Agent needs a runnable base before it gains domain-specific agents. Phase-00 must
prove the transport-to-model path, session state, normalized output, streaming, observability,
and failure semantics without introducing water-engineering behavior or coupling to Dayu
Tiangong.

The official OpenAI Agents SDK already owns the OpenAI agent loop, model invocation, streaming,
tool orchestration primitives, and tracing integration. Reimplementing those capabilities would
create a second protocol and increase migration risk.

## Decision

Use one application-owned `SupervisorAgent` and a provider-neutral `ModelProvider` contract.
The OpenAI implementation creates one official SDK `Agent` and invokes it through `Runner` or
`Runner.run_streamed`. The default test/development path uses `FakeModelProvider`.

The Supervisor owns only:

- input/output Guardrail execution;
- `SessionStore` interaction;
- provider invocation;
- controlled Tool Registry integration;
- conversion to `AgentResult`;
- local tracing and normalized exception propagation.

API and CLI are transports. They do not own conversation state or model logic. HTTP Request and
SDK result objects do not enter Agent Core.

## Why one Supervisor

Phase-00 has one responsibility: establish a trustworthy runtime. Multiple specialist agents
would add handoff state, routing ambiguity, additional prompts, and evaluation requirements
before any specialist domain contract exists. One focused agent keeps failure ownership and
session semantics testable.

## Why no LangGraph

There is no durable branching workflow, checkpointed graph, or complex multi-agent state machine
in the current requirements. The official SDK plus small explicit boundaries satisfies the
implemented behavior. Adding LangGraph now would duplicate orchestration and persistence
concepts without an accepted use case.

## Consequences

- Runtime tests use no credentials or tokens.
- OpenAI SDK changes remain localized to one provider.
- New providers implement `ModelProvider`; they do not modify Supervisor.
- Domain specialists and handoffs remain deferred until their contracts and evals exist.
- Official SDK tracing may be enabled, but local `TraceContext` keeps the project operable without
  a SaaS tracing backend.
