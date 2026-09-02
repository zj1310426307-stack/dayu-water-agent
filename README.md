# Dayu Water Agent

Dayu Water Agent is an independent foundation for an engineering AI agent runtime.

> Dayu Water Agent 当前处于独立 Agent Runtime 基础架构阶段。

The repository is deliberately small: one Supervisor, a replaceable model-provider boundary,
process-local sessions, a schema-first Tool Registry, FastAPI, a developer CLI, structured
logging, local tracing, optional SQLAlchemy/Alembic infrastructure, tests, and Docker. It does
not yet provide hydraulic-engineering domain capability.

## Current capabilities

- One `SupervisorAgent` shared by CLI and REST transports.
- `ModelProvider` abstraction with:
  - `FakeModelProvider` for deterministic, credential-free development and CI.
  - `OpenAIModelProvider` backed by the official OpenAI Agents SDK `Agent + Runner` path.
- `SessionStore` contract and concurrency-safe `InMemorySessionStore`.
- Stable `AgentContext`, `AgentResult`, usage, status, warning, and tool-call schemas.
- Schema-first `ToolRegistry` with name collision checks, Pydantic input/output validation,
  permission enforcement, human-confirmation policy, timeout, and normalized failures.
- Two read-only deterministic proof tools: `system.health` and `system.echo`.
- Input, output, and tool Guardrail contracts with fail-closed Phase-00 defaults.
- REST chat, session endpoints, native-provider SSE streaming, CLI, JSON logs, and local traces.
- Optional minimal PostgreSQL tables and Alembic migration without runtime coupling.

## Non-goals

Phase-00 does not implement a knowledge base, RAG, flood evaluation, hydrology, hydraulics,
GIS, report generation, PostGIS business queries, Dayu Tiangong integration, production IAM,
production-grade persistent sessions, complex multi-agent orchestration, PLC/SCADA access, or
automatic control. This system is not an automatic control system.

It intentionally exposes no shell tool, Python execution tool, SQL tool, arbitrary filesystem
tool, or Dangerous Tool.

## Architecture

```text
CLI / REST API
      │
      ▼
SupervisorAgent
      │
      ├── SessionStore ──► InMemorySessionStore
      ├── ToolRegistry ──► system.health / system.echo
      ├── Guardrails
      ├── TraceContext + JSON logging
      │
      ▼
ModelProvider
      ├── FakeModelProvider
      └── OpenAIModelProvider ──► OpenAI Agents SDK
```

HTTP request objects, OpenAI SDK result objects, and SQLAlchemy models do not enter Agent Core.
The provider owns model configuration and credentials. The runtime receives an already
constructed provider.

Future Dayu Tiangong access must remain outside the core:

```text
Dayu Water Agent ── Tool Contract ── MCP / REST Adapter ── Dayu Tiangong
```

See [ADR-0001](docs/architecture/ADR-0001-agent-runtime.md),
[ADR-0002](docs/architecture/ADR-0002-tool-contract.md), and
[ADR-0003](docs/architecture/ADR-0003-dayu-integration-boundary.md).

## Quick start

Requirements: Python 3.12 or newer.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -e ".[dev]"
```

The default provider is fake and requires no API key:

```bash
dayu-agent version
dayu-agent health
dayu-agent chat --message "hello"
dayu-agent chat
```

Start the API:

```bash
uvicorn dayu_agent.api.app:app --host 0.0.0.0 --port 8000
```

Then request `http://127.0.0.1:8000/health` and
`http://127.0.0.1:8000/ready` separately.

## Environment

Copy `.env.example` to `.env` for local changes. `.env` is ignored by Git.

| Variable | Default | Purpose |
|---|---|---|
| `DAYU_AGENT_ENV` | `development` | Runtime environment label |
| `DAYU_AGENT_HOST` | `0.0.0.0` | API bind host |
| `DAYU_AGENT_PORT` | `8000` | API bind port |
| `MODEL_PROVIDER` | `fake` | `fake` or `openai` |
| `MODEL_NAME` | `gpt-5.6` | Explicit provider model |
| `OPENAI_API_KEY` | empty | Required only for `MODEL_PROVIDER=openai` |
| `DATABASE_URL` | local PostgreSQL URL | Optional persistence infrastructure |
| `LOG_LEVEL` | `INFO` | JSON log threshold |
| `PROVIDER_TIMEOUT_SECONDS` | `60` | Model-call timeout |
| `SDK_TRACING_ENABLED` | `false` | Opt-in official SDK tracing |

Settings diagnostics never include `OPENAI_API_KEY`. Official SDK tracing is disabled by
default and sensitive trace inclusion remains disabled when tracing is enabled.

## API

| Method | Path | Semantics |
|---|---|---|
| `GET` | `/health` | Process liveness only |
| `GET` | `/ready` | Runtime and provider configuration readiness |
| `POST` | `/api/v1/sessions` | Create an in-memory session |
| `GET` | `/api/v1/sessions/{session_id}` | Read session metadata and messages |
| `POST` | `/api/v1/chat` | Run one normalized Supervisor turn |
| `POST` | `/api/v1/chat/stream` | Stream native provider deltas over SSE |

Chat request:

```json
{
  "message": "hello",
  "session_id": null,
  "metadata": {}
}
```

Errors always use `error_code`, `message`, `request_id`, and optional `details`; stack traces,
SDK errors, keys, secrets, and internal paths are not returned.

The explicit `/tool system.echo {"text":"hello"}` chat form is a Phase-00 proof that the
Supervisor traverses Guardrails and Tool Registry. It is not arbitrary command execution.

## CLI

```text
dayu-agent version
dayu-agent health
dayu-agent chat [--message TEXT] [--session-id ID]
python -m dayu_agent version
```

Interactive sessions are process-local. A session identifier is not durable across separate CLI
processes because production session persistence is intentionally deferred.

## Database infrastructure

`agent_sessions` and `agent_messages` are the only Phase-00 tables. The running Agent uses
`InMemorySessionStore` by default and starts without PostgreSQL.

```bash
alembic upgrade head
```

Adding a persistent store later must implement `SessionStore`; it must not change Supervisor
logic or make direct database access available to the model.

## Docker

```bash
docker compose config
docker compose up --build
```

Compose defines `agent-api` and PostgreSQL. The API does not depend on a successful database
connection in Phase-00. Container `running` is not considered sufficient evidence: verify the
real `/health` endpoint.

## Testing

Default tests do not consume LLM tokens:

```bash
ruff check .
pytest
mypy src/dayu_agent tests
pytest --cov=dayu_agent --cov-report=term-missing --cov-fail-under=80
```

The live provider smoke test is skipped unless both `RUN_OPENAI_INTEGRATION=1` and
`OPENAI_API_KEY` are explicitly set.

## Roadmap

- AGENT-PHASE-00 — Foundation (current)
- AGENT-PHASE-01 — Production-grade Agent Runtime
- AGENT-PHASE-02 — Tool Runtime & Permissions
- AGENT-PHASE-03 — MCP Client / Server
- AGENT-PHASE-04 — Knowledge & Evidence Engine
- AGENT-PHASE-05 — Flood Evaluation Agent
- AGENT-PHASE-06 — Report Agent
- AGENT-PHASE-07 — Hydraulic Agent
- AGENT-PHASE-08 — Evaluation & Guardrails
- AGENT-PHASE-09 — Mock Dayu Provider
- AGENT-PHASE-10 — Dayu Tiangong MCP Gateway
- AGENT-PHASE-11 — Formal Dayu Integration
- AGENT-PHASE-12 — Production Hardening

Roadmap entries are plans, not current capabilities.
