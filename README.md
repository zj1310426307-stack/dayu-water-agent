# Dayu Water Agent

Dayu Water Agent 0.2.0 is an independent, production-oriented Agent Runtime foundation.

> 当前阶段是 AGENT-PHASE-01：可靠运行时，不包含水利业务能力，也不是自动控制系统。

The repository deliberately contains one `SupervisorAgent`, a replaceable model boundary,
durable session/run state, a small safe Tool Registry, REST/CLI transports, and operational
instrumentation. It does not contain RAG, domain agents, Dayu Tiangong access, or a workflow
graph.

## Implemented

- Persistent PostgreSQL sessions, committed messages, `AgentRun` state, and stream events.
- Atomic success-only conversation commits: a user/assistant pair appears together or not at
  all; failed, cancelled, and interrupted runs never pollute future context.
- `Idempotency-Key` on chat and streaming entry points. The same key and canonical request
  returns the same run; changed request semantics return HTTP 409.
- Fail-fast same-session concurrency with a PostgreSQL row lock and a partial unique index;
  different sessions execute concurrently.
- True process-local task cancellation with transactional terminal-state arbitration.
- One bounded retry owner in the Supervisor. The OpenAI HTTP client has retries disabled and
  the Agents SDK runner uses its non-retrying default.
- Run-bound durable SSE with monotonic event IDs, replay through `Last-Event-ID`, terminal
  events, and retention cleanup support.
- Startup reconciliation of active runs from prior worker instances to `interrupted`; no
  ambiguous provider execution is automatically replayed.
- Dependency-aware readiness, graceful shutdown, JSON correlation logs, internal metrics, and
  optional OpenTelemetry export.
- Fake and OpenAI providers, CLI compatibility, Alembic migrations, CI, and Docker Compose.

## Experimental or limited

- Cancellation owns real provider tasks only inside the current API worker. A request routed to
  another live worker returns `CANCELLATION_UNAVAILABLE`; there is no distributed task queue.
- Stream retention is finite and pruning is an explicit maintenance operation.
- OpenTelemetry is optional. With `OTEL_ENABLED=false` or missing optional packages, local JSON
  tracing remains active and startup does not depend on a collector.
- Usage is accumulated when the provider exposes usage. The current SDK cannot reliably report
  token usage for every failed attempt, so those tokens may be absent from run totals.

## Explicit non-goals

There is no knowledge base, RAG, flood evaluation, hydrology, hydraulics, GIS, report agent,
Dayu Tiangong integration, enterprise IAM, distributed queue, cross-region HA, PLC/SCADA
access, shell tool, SQL tool, Python execution tool, arbitrary filesystem tool, or automatic
device control.

## Architecture

```text
CLI / REST / SSE
        │
        ▼
SupervisorAgent ── RuntimeStore contract
        │                 ├── InMemorySessionStore (tests/offline development)
        │                 └── SQLAlchemyRuntimeStore ── PostgreSQL
        ├── Guardrails
        ├── ToolRegistry ── system.health / system.echo
        ├── RetryBudget + task ownership
        └── JSON traces / metrics / optional OTEL
        │
        ▼
ModelProvider
        ├── FakeModelProvider
        └── OpenAIModelProvider ── OpenAI Agents SDK
```

Provider SDK objects and SQLAlchemy models remain inside their adapters. HTTP objects do not
enter the runtime core. Future Dayu Tiangong connectivity must remain behind a Tool/MCP/REST
adapter boundary.

Architecture decisions:

- [ADR-0001](docs/architecture/ADR-0001-agent-runtime.md)
- [ADR-0002](docs/architecture/ADR-0002-tool-contract.md)
- [ADR-0003](docs/architecture/ADR-0003-dayu-integration-boundary.md)
- [ADR-0004](docs/architecture/ADR-0004-persistent-session-and-run-state.md)
- [ADR-0005](docs/architecture/ADR-0005-idempotency-and-session-concurrency.md)
- [ADR-0006](docs/architecture/ADR-0006-cancellation-and-retry-budget.md)
- [ADR-0007](docs/architecture/ADR-0007-reliable-streaming.md)

## Local development

Requirements: Python 3.12 or newer.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -e ".[dev]"
dayu-agent version
dayu-agent health
dayu-agent chat --message "hello"
```

Development defaults to `MODEL_PROVIDER=fake` and `SESSION_STORE=memory`, so it needs neither
credentials nor PostgreSQL.

```bash
uvicorn dayu_agent.api.app:app --host 0.0.0.0 --port 8000
```

Use `/health` for process liveness and `/ready` for runtime, store, and provider readiness.

## Production-like start

Copy `.env.example` to `.env`, replace the development database password, then run:

```bash
docker compose config
docker compose up --build -d
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
```

Compose waits for PostgreSQL, applies `alembic upgrade head`, starts the API as a non-root user,
and uses `/ready` for container health. Production configuration requires
`SESSION_STORE=postgres`; the application never silently falls back to memory.

For a non-container PostgreSQL deployment:

```bash
export SESSION_STORE=postgres
export DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DB
python -m alembic upgrade head
uvicorn dayu_agent.api.app:app --host 0.0.0.0 --port 8000
```

## Configuration

| Variable | Default | Purpose |
|---|---:|---|
| `DAYU_AGENT_ENV` | `development` | `development`, `test`, or `production` |
| `MODEL_PROVIDER` | `fake` | `fake` or `openai` |
| `MODEL_NAME` | `gpt-5.6` | Explicit provider model |
| `OPENAI_API_KEY` | empty | Required only for OpenAI |
| `SESSION_STORE` | `memory` | `memory` or `postgres`; production requires PostgreSQL |
| `DATABASE_URL` | empty | Required for the PostgreSQL store |
| `DB_POOL_SIZE` | `5` | Persistent connection pool size |
| `DB_MAX_OVERFLOW` | `10` | Temporary pool overflow |
| `PROVIDER_TIMEOUT_SECONDS` | `60` | Per-provider adapter timeout |
| `RETRY_MAX_ATTEMPTS` | `3` | Total provider attempts per run |
| `RETRY_MAX_ELAPSED_SECONDS` | `30` | Whole runtime retry wall-clock budget |
| `RETRY_BASE_DELAY_SECONDS` | `0.25` | Initial exponential backoff |
| `RETRY_MAX_DELAY_SECONDS` | `2` | Backoff cap |
| `RETRY_JITTER` | `true` | Bounded multiplicative jitter |
| `STREAM_EVENT_RETENTION_SECONDS` | `86400` | Durable terminal-event retention policy |
| `MAX_REQUEST_BYTES` | `1048576` | Declared HTTP body limit |
| `OTEL_ENABLED` | `false` | Enable optional OTEL SDK integration |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | empty | Optional OTLP/HTTP trace endpoint |

Safe configuration summaries exclude API keys, database URLs, passwords, and OTEL secrets.

## Runtime semantics

`AgentRun` follows this state machine:

```text
pending ──► running ──► completed
   │           ├──────► failed
   │           ├──────► cancelled
   │           └──────► interrupted
   ├──────────────────► failed
   ├──────────────────► cancelled
   └──────────────────► interrupted
```

Terminal states are immutable. One session may have only one `pending` or `running` run. The
concurrency policy is fail-fast (`SESSION_BUSY`), not a hidden queue.

Idempotency hashes canonical `message`, `session_id`, `user_id`, and `metadata`. Transport
request IDs and timestamps are intentionally excluded. Keys are global within the selected
runtime store and must not contain secrets.

Retry classification is explicit. Timeouts, connection failures, rate limits, and server-side
provider failures may retry within both attempt and elapsed-time caps. Invalid/auth/policy
failures do not retry. Streaming retries only before the first persisted delta.

## API

| Method | Path | Semantics |
|---|---|---|
| `GET` | `/health` | Process liveness only |
| `GET` | `/ready` | Runtime, store, and provider readiness |
| `POST` | `/api/v1/sessions` | Create a session |
| `GET` | `/api/v1/sessions/{session_id}` | Read committed ordered history |
| `POST` | `/api/v1/chat` | Start or replay a complete run |
| `POST` | `/api/v1/chat/stream` | Start or replay a run and tail SSE |
| `GET` | `/api/v1/runs/{run_id}` | Query durable run state |
| `POST` | `/api/v1/runs/{run_id}/cancel` | Cancel an owned active run idempotently |
| `GET` | `/api/v1/runs/{run_id}/stream` | Replay/tail SSE after `Last-Event-ID` |

Chat endpoints accept an optional `Idempotency-Key` header. Streaming responses include
`X-Run-ID`. Durable event names are `run.started`, `response.delta`, `response.completed`,
`run.failed`, `run.cancelled`, and `run.interrupted`; every frame has a monotonically increasing
integer `id`.

Errors always contain `error_code`, `message`, `request_id`, and optional safe `details`. Raw
stack traces, SDK messages, secrets, and internal paths are not returned.

The committed OpenAPI artifact is [docs/openapi.json](docs/openapi.json). Regenerate and check it
with:

```bash
python scripts/generate_openapi.py
python scripts/generate_openapi.py --check
```

## CLI

```text
dayu-agent version
dayu-agent health
dayu-agent chat [--message TEXT] [--session-id ID]
python -m dayu_agent version
```

The CLI initializes and closes the selected store. Separate CLI invocations can share sessions
when `SESSION_STORE=postgres`.

## Testing

Default tests never invoke a paid model. PostgreSQL integration requires an explicit disposable
database URL:

```bash
ruff check .
mypy src/dayu_agent
pytest
DAYU_TEST_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DB pytest -m integration
pytest --cov=dayu_agent --cov-report=term-missing --cov-fail-under=80
pip check
python -m compileall -q src tests
```

The live OpenAI smoke remains skipped unless both `RUN_OPENAI_INTEGRATION=1` and
`OPENAI_API_KEY` are explicitly provided.

## Roadmap

- AGENT-PHASE-00 — Foundation (complete)
- AGENT-PHASE-01 — Production-grade runtime (current)
- AGENT-PHASE-02 — Tool Runtime & Permissions (planned)
- Later phases — MCP, evidence, domain agents, Dayu integration, and hardening (planned)

Roadmap entries are plans, not current capabilities.
