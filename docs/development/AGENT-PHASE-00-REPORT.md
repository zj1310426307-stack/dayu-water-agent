# AGENT-PHASE-00 Development Report

Date: 2026-09-02  
Repository: `dayu-water-agent`  
Branch: `main`  
Phase status: PASS for required software and Compose configuration gates; external live-model and
Docker Engine execution are explicitly not verified.

## 1. Executive Summary

AGENT-PHASE-00 established an independent, credential-free-by-default Dayu Water Agent runtime.
The delivered repository runs without Dayu Tiangong, PostgreSQL, or an API key. CLI and FastAPI
share one Supervisor runtime. The model, session, tool, guardrail, and tracing boundaries are
replaceable and covered by deterministic tests.

No water-engineering business capability was implemented. The system is a foundation and is not
an automatic control system.

## 2. Repository Structure

```text
dayu-water-agent/
├── apps/api/main.py
├── apps/cli/main.py
├── src/dayu_agent/
│   ├── agents/            # single Supervisor
│   ├── api/               # input/error/response transport layer
│   ├── config/            # environment settings and credential validation
│   ├── database/          # optional SQLAlchemy infrastructure
│   ├── guardrails/        # provider-independent contracts
│   ├── mcp/               # reserved official-SDK integration boundary
│   ├── memory/            # SessionStore + in-memory implementation
│   ├── observability/     # JSON logging + local TraceContext
│   ├── providers/         # provider contract, Fake, OpenAI Agents SDK
│   ├── runtime/           # AgentContext and AgentResult contracts
│   └── tools/             # schema-first registry, policy, built-ins
├── tests/unit/
├── tests/integration/
├── alembic/
├── docs/architecture/
├── docs/development/
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

## 3. Implemented Capabilities

- Credential-free deterministic chat through Fake Provider.
- Optional OpenAI provider through the official Agents SDK.
- Multi-turn in-memory sessions.
- Provider-native SSE event forwarding.
- Explicit schema-first read-only tool proof path.
- Structured results, errors, logs, trace fields, and request correlation.
- REST and developer CLI surfaces.
- Optional two-table SQLAlchemy/Alembic persistence foundation.
- Docker API/PostgreSQL topology with real application health checks.

## 4. Agent Runtime

`SupervisorAgent` receives normalized text and metadata, resolves a `SessionStore`, builds
`AgentContext`, runs input Guardrails, persists the user message, invokes a registered safe tool
or `ModelProvider`, validates output, persists the assistant response, and returns `AgentResult`.

HTTP Request objects, SQLAlchemy models, and provider SDK objects do not cross into the runtime.
Unknown or invalid state is rejected rather than guessed.

## 5. Provider Architecture

`ModelProvider` defines `health`, `run`, and `stream`. Runtime never imports OpenAI SDK or reads
`OPENAI_API_KEY`.

`FakeModelProvider` is deterministic and powers default development/CI. `OpenAIModelProvider`
uses `OpenAIProvider`, `Agent`, `RunConfig`, `Runner.run`, and `Runner.run_streamed` from installed
`openai-agents 0.22.0`. It owns the API key, explicit model, timeout, SDK tracing policy, and SDK
result normalization.

The implementation was aligned with the official
[Agents SDK quickstart](https://developers.openai.com/api/docs/guides/agents/quickstart) and
[models/providers guide](https://developers.openai.com/api/docs/guides/agents/models), then
verified against installed class signatures instead of older tutorials.

## 6. Session Architecture

`SessionStore` declares create, get, append, list, and clear. `InMemorySessionStore` uses an async
lock, rejects duplicate or unknown identifiers, retains insertion order, and returns defensive
copies. Supervisor depends only on the interface.

SQLAlchemy tables do not imply a persistent runtime store. A production PostgreSQL store remains
deferred and can be added by implementing the same contract.

## 7. Tool Runtime

`ToolDefinition` declares name, description, input/output Pydantic models, permission, risk,
timeout, and handler. `ToolRegistry` supports register, unregister, get, list, and execute.

Execution checks dotted names, duplicate names, schema classes, known permission/risk enums,
grants, human confirmation, input schema, timeout, handler failure, and output schema. Pydantic
errors are sanitized so raw submitted values are not returned.

Only `system.health` and `system.echo` are built in. Both are deterministic and read-only.

## 8. Permission Model

The enum contains `READ`, `ANALYZE`, `CREATE`, `MODIFY`, `EXECUTE`, and `DANGEROUS`. Defaults
grant only `READ` and `ANALYZE`. Empty explicit grants deny everything. `MODIFY` and `EXECUTE`
require both an explicit grant and confirmation. Unknown enums fail closed. Dangerous permission
or risk definitions cannot be registered in Phase-00.

## 9. API

Implemented endpoints:

- `GET /health`: process liveness only.
- `GET /ready`: runtime and provider configuration readiness.
- `POST /api/v1/sessions`: create session.
- `GET /api/v1/sessions/{session_id}`: inspect session and messages.
- `POST /api/v1/chat`: normalized chat response.
- `POST /api/v1/chat/stream`: SSE native provider events.

Every request receives `X-Request-ID`. Domain and request-validation errors use one safe error
shape. Raw stack traces, SDK error strings, credentials, and internal paths are not returned.

## 10. CLI

Both installed `dayu-agent` and `python -m dayu_agent` support:

- `version`;
- `health`;
- `chat --message ...`;
- interactive `chat`.

CLI output is transport output; operational logs remain structured JSON.

## 11. MCP Boundary

No mock MCP protocol or Dayu-specific MCP implementation exists. `dayu_agent.mcp` reserves the
integration location. ADR-0003 requires future integration to use the official MCP SDK behind
Tool Contract and forbids Tiangong imports, direct database access, ORM imports, or internal
solver calls.

## 12. Security Controls

- `.env` and secret-bearing local variants are ignored by Git.
- OpenAI configuration fails before runtime if selected without a key.
- Safe settings summaries omit secrets and database credentials.
- JSON logging redacts common credential field names.
- SDK tracing is off by default and sensitive trace inclusion is disabled.
- Tool names, schemas, permissions, risks, confirmation, timeout, and outputs fail closed.
- No Shell, Python execution, SQL, filesystem write, business, or Dangerous Tool exists.
- No `dayu_tiangong` import exists.
- API errors contain no stack trace or raw SDK exception.

## 13. Tests

The suite covers:

- Config defaults, missing OpenAI key, environment overrides, safe summaries.
- Session create/get/append/list/clear, ordering, duplicate and unknown IDs.
- Runtime normal answer, multi-turn context, empty input, provider failure, output Guardrail,
  explicit tool path, and streaming persistence.
- Tool lifecycle, collision, unknown tool, names, input/output schemas, permission, empty grants,
  confirmation, unknown enum, Dangerous denial, timeout, and handler failure.
- API health/readiness distinction, chat, multi-turn session, SSE, error shape, provider failure,
  OpenAPI routes.
- CLI version, health, chat, and safe error output.
- OpenAI adapter success/failure/stream normalization without network use.
- Database metadata and static security boundaries.

## 14. Verification Results

| Verification | Result |
|---|---|
| Python | PASS — 3.12.13 |
| Ruff | PASS — all checks passed |
| pytest | PASS — 43 passed, 1 optional live test skipped |
| Coverage | PASS — 92.64%, required 80% |
| mypy | PASS — 49 source/test files |
| Compile/import | PASS |
| API smoke | PASS — real local Uvicorn HTTP, health/ready/session/two chat turns |
| CLI smoke | PASS — installed entry point and `python -m` |
| Tool Registry | PASS — full lifecycle and failure tests |
| Session multi-turn | PASS — same ID, four ordered messages |
| Alembic offline SQL | PASS — revision 0001 generated |
| Dependency check | PASS — no broken requirements |
| Independence/security scan | PASS — no Tiangong imports or prohibited tool modules |
| Docker Compose config | PASS — CLI 29.7.2, Compose 5.4.0, exit 0 |
| Docker image/up/health | NOT VERIFIED — Docker Desktop Linux Engine was not running |
| Live OpenAI call | NOT VERIFIED — no explicit opt-in or API key; no tokens consumed |

The full workspace verification evidence is stored outside the repository in
`06_验证记录/2026-09-02_AGENT-PHASE-00验证记录.md`.

## 15. Known Limitations

- 当前没有知识库。
- 当前没有防洪评价专业能力。
- 当前没有水动力工具。
- 当前没有 GIS 工具。
- 当前没有接入大禹·天工。
- 当前没有生产 IAM。
- 当前没有生产级持久会话；默认会话随进程结束而消失。
- 当前不是自动控制系统。
- OpenAI live behavior was not tested in this delivery because the test is explicitly opt-in.
- Docker image build and container health were not tested because the Linux Engine was stopped.
- SSE has no durable resume/checkpoint mechanism for disconnected clients.
- The explicit read-only Tool proof does not yet expose general model-selected business tools.

## 16. Deferred Work

- Persistent `SessionStore`, concurrency and lifecycle hardening.
- Production authentication, authorization, quotas, and audit retention.
- Approved model-selected tool adapter and human-in-the-loop lifecycle.
- Official MCP client/server implementation and transport security.
- OpenTelemetry exporter configuration and operational dashboards.
- Deployment secrets, TLS, resilience, and load testing.
- Domain capabilities only after evidence and permission contracts are approved.

## 17. Recommended Phase-01

Proceed with AGENT-PHASE-01: Production-grade Agent Runtime. Priorities should be persistent
session semantics, idempotency, cancellation, durable streaming state, concurrency/load tests,
provider retry policy with budgets, production IAM boundary, and deployment observability. Do not
start domain tools or Tiangong integration until those runtime controls and evaluations pass.
