# ADR-0002: Schema-First, Fail-Closed Tool Contract

- Status: Accepted
- Date: 2026-09-02
- Scope: AGENT-PHASE-00

## Context

Future agents will need to query engineering services and start controlled workflows. Direct
database, solver, shell, or filesystem access would couple the model to implementation details
and make authorization, audit, validation, and human confirmation inconsistent.

## Decision

Every external capability must enter Agent Core as a registered `ToolDefinition` containing:

- stable dotted name and description;
- Pydantic input and output models;
- explicit permission and risk level;
- bounded timeout;
- async handler.

`ToolRegistry` is the sole execution path. It rejects duplicate or unknown names, validates both
input and output, enforces granted permissions and confirmation, times out handlers, and maps
handler failures to domain errors. It never silently replaces an existing tool.

Permissions are `READ`, `ANALYZE`, `CREATE`, `MODIFY`, `EXECUTE`, and `DANGEROUS`. Phase-00 grants
only `READ` and `ANALYZE` by default. `MODIFY` and `EXECUTE` require an explicit grant and human
confirmation. `DANGEROUS` tools and Dangerous risk definitions cannot be registered.

## Why the Agent cannot access databases directly

A database schema is an internal storage contract, not a model-facing business capability.
Direct access would bypass service invariants, authorization, audit evidence, transaction rules,
and future Dayu Tiangong boundaries. SQLAlchemy models therefore remain optional persistence
infrastructure and are never passed to Supervisor or provider.

## Why all external capabilities are tools

One contract gives each operation a discoverable schema, permission, risk, timeout, error model,
and audit identity. MCP and REST integrations can later implement handlers without changing the
Agent Runtime.

## Why Schema First

Unconstrained dictionaries defer errors until handler execution and create ambiguous prompts.
Pydantic models produce deterministic validation at both sides of the boundary and make OpenAPI,
MCP, tests, and future approval UI derivable from the same contract.

## Phase-00 proof surface

Only `system.health` and `system.echo` are registered. Both are deterministic, read-only, and
side-effect free. There are no shell, Python execution, SQL, filesystem-write, or business tools.

## Consequences

- Unknown tool, unknown schema, permission failure, timeout, and handler failure all fail closed.
- Tool failures cannot be represented as fabricated agent success.
- Future adapters remain replaceable and auditable.
- Model-driven tool selection beyond this safe proof surface is deferred until the permission and
  human-approval lifecycle is production-ready.
