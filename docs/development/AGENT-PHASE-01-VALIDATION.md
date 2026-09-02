# AGENT-PHASE-01 Validation

- Validation date: 2026-09-02 (Asia/Shanghai)
- Repository: `dayu-water-agent`
- Branch: `phase/agent-phase-01-runtime`
- Baseline: `57f402b0bd8d24175767b1ca97f2a66e4a5634f3`
- Validated implementation: `ee17324a74b5074b1e4c482182a1eff12b06b1d2`
- Result: PASS

## Environment

| Component | Verified value |
|---|---|
| Host | Windows, Codex workspace |
| Python | 3.12.13 |
| Dayu Water Agent | 0.2.0 |
| FastAPI | 0.141.1 |
| OpenAI Python | 3.7.0 |
| OpenAI Agents SDK | 0.22.0 |
| SQLAlchemy | 2.0.52 |
| psycopg | 3.3.5 |
| Docker Desktop | 4.88.1 |
| Docker Engine | 29.7.2 |
| Docker Compose | 5.4.0 |
| PostgreSQL | 17.11 |

The Python environment and caches were isolated under the project `99_临时文件` directory.
PostgreSQL validation used disposable Docker state, never a production database.

## Commands and results

| Validation | Command summary | Exit/result |
|---|---|---|
| Lint | `python -m ruff check .` | PASS, exit 0 |
| Types | `python -m mypy src/dayu_agent` | PASS, 46 source files, exit 0 |
| OpenAPI sync | `python scripts/generate_openapi.py --check` | PASS, exit 0 |
| Full test/coverage | `python -m pytest --cov=dayu_agent --cov-report=term-missing --cov-fail-under=80` | 74 passed, 1 skipped, 86.34%, exit 0 |
| Dependencies | `python -m pip check` | No broken requirements, exit 0 |
| Compilation | `python -m compileall -q src tests` | PASS, exit 0 |
| Migration up | `python -m alembic upgrade head` | PASS on real PostgreSQL |
| Migration down | `python -m alembic downgrade base` | PASS on real PostgreSQL |
| Migration re-up | `python -m alembic upgrade head` | PASS, current `0002` |
| Compose config | `docker compose config --quiet` | PASS |
| Image build | `docker compose build agent-api` | PASS, final source rebuilt |
| Compose start | `docker compose up -d` | PASS |

The skipped test is `tests/integration/test_openai_live.py`; it requires explicit
`RUN_OPENAI_INTEGRATION=1` plus a real API key and is not a core Phase-01 gate.

## Gate A–N evidence

| Gate | Evidence | Status |
|---|---|---|
| A Phase-00 regression | Original 43 behaviors retained in the expanded 74-pass suite; security tests remain | PASS |
| B persistent session | Real PostgreSQL API instance restarted; session retained two committed messages and its completed run | PASS |
| C idempotency | Concurrent same-key tests return one run; Fake provider call count is one; PostgreSQL lock-wait race covered | PASS |
| D conflict | Same key with changed payload returns `IDEMPOTENCY_CONFLICT`, HTTP 409 | PASS |
| E concurrency | Two same-session reservations yield one owner plus `SESSION_BUSY`; 50 independent sessions complete concurrently | PASS |
| F cancellation | Blocking Fake task becomes `cancelled`, is awaited, emits one terminal event, and writes no messages | PASS |
| G retry | Two retryable failures then success produces three attempts; exhaustion stops exactly at cap; terminal error attempts once | PASS |
| H reliable streaming | Durable sequences, consumer disconnect, `Last-Event-ID` resume, no duplicate/gap, correct final; Docker HTTP SSE replay also passed | PASS |
| I crash reconciliation | Real PostgreSQL `running` row owned by old worker becomes `interrupted` at new-worker startup | PASS |
| J observability | Test captures request, run, session, and trace IDs; attempt/retry logs, metrics, and optional OTEL contracts covered | PASS |
| K database failure | Explicit PostgreSQL store with unavailable database returns `/ready` 503 and chat `DATABASE_UNAVAILABLE` 503; no fallback | PASS |
| L Docker | Build, migration, up, health, ready, non-root UID, real SSE, API restart, and persistent recovery passed | PASS |
| M quality | Ruff, mypy, pytest, 86.34% coverage, pip check, compileall, and OpenAPI sync passed | PASS |
| N security | No Tiangong import, dangerous execution tools, or real secrets; static scan and boundary tests passed | PASS |

## API smoke

The final Compose image returned:

- `/health`: `alive`;
- `/ready`: `ready`, with runtime/store/provider all true;
- chat: one completed fake-provider run;
- session query after API restart: two committed messages;
- run query after API restart: `completed`;
- streaming: `run.started`, `response.delta`, and `response.completed` with integer IDs;
- resume after `Last-Event-ID: 1`: omitted event 1 and returned later delta/final events.

The final container health was `healthy`, `dayu-agent version` returned `0.2.0`, PostgreSQL
reported `17.11`, Alembic reported `0002`, and the API process ran as `uid=999(dayu)`.

## Concurrency and performance smoke

`test_postgres_api_handles_fifty_parallel_independent_sessions` completed 50 independent chat
runs against real PostgreSQL with 50 unique run IDs and no 500, duplicate sequence, or deadlock.
The focused test took 3.99 seconds on this Windows/Docker development host (about 12.5 completed
chat runs/second). This is a correctness-oriented local baseline, not a production capacity claim.

## Cancellation and retry smoke

- Cancellation of an indefinitely blocked provider await stopped the task and committed exactly
  one `run.cancelled` event.
- Double cancellation was idempotent.
- Cancellation of a completed run did not rewrite completion.
- Cancellation during retry backoff prevented the next attempt.
- Completion/cancellation race produced one terminal state/event.
- A streamed retryable failure after the first persisted delta did not retry.

## Security review

- Secrets: no API key, private key, or token pattern found; settings summaries exclude URLs and
  credentials.
- SQL injection: runtime queries use static SQLAlchemy expressions; no user value is interpolated
  into SQL text.
- Error leakage: domain/API regression tests reject raw provider messages, tracebacks, and paths.
- Races: database row locks plus unique constraints cover active-run, sequence, and idempotency
  arbitration.
- Bounds: attempts, elapsed time, backoff, request size, tool timeouts, and terminal stream event
  retention are bounded.
- Permissions: only `system.health` and `system.echo` proof tools remain; shell, SQL, Python exec,
  arbitrary filesystem write, and automatic-control tools are absent.

## Docker status

PASS. Both `agent-api` and `postgres` were healthy after the final rebuild. The API was restarted
and force-recreated while its named PostgreSQL volume remained; prior session/run state was still
queryable. Validation services and their disposable volume are removed after evidence capture.

## OpenAI live status

NOT VERIFIED. No explicit paid-call opt-in and credential were supplied. Fake Provider covers all
core runtime gates. The OpenAI adapter is unit-tested with the Agents SDK boundary and confirms its
underlying OpenAI client uses `max_retries=0`.
