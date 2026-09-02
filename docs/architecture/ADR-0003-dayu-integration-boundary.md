# ADR-0003: Dayu Tiangong Integration Boundary

- Status: Accepted
- Date: 2026-09-02
- Scope: Long-term architecture invariant

## Context

Dayu Water Agent will eventually consume GIS, hydraulic-model, and project capabilities provided
by Dayu Tiangong. Sharing internal Python modules, ORM entities, database connections, or solver
objects would prevent independent deployment and make the Agent capable of bypassing Tiangong's
authorization and workflow rules.

## Decision

Dayu Water Agent remains an independent repository and process. Long-term integration occurs only
through an MCP client/server or REST adapter behind Tool Contract.

```text
              Dayu Water Agent
                     │
                     │ MCP / REST
                     ▼
              Dayu Tiangong
                     │
        ┌────────────┼────────────┐
        ▼            ▼            ▼
       GIS       Hydraulic      Project
                  Model
```

The following are prohibited:

- `import dayu_tiangong` or copied Tiangong modules;
- direct access to Tiangong PostgreSQL/PostGIS;
- importing Tiangong ORM entities;
- calling internal solver Python objects;
- copying GIS, hydraulic, or project business logic into Agent Core.

The following flow is required:

```text
SupervisorAgent
      ▼
ToolRegistry
      ▼
MCP / REST adapter
      ▼
Dayu Tiangong public capability
```

## Phase-00 state

No Dayu Tiangong adapter, MCP server, MCP client, or mock Dayu protocol is implemented. The
`dayu_agent.mcp` package records the reserved boundary only. When Phase-03 begins, it must use the
official MCP SDK rather than inventing a protocol.

## Consequences

- Both systems can version, test, deploy, and scale independently.
- Tiangong remains owner of its data, solvers, workflows, and authorization.
- Agent permissions and schemas remain explicit at every integration point.
- Integration work becomes an added adapter capability rather than a core rewrite.
