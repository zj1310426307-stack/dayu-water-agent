# AGENT-PHASE-01-RC1 Independent Audit

## 1. Baseline

- Repository: `https://github.com/zj1310426307-stack/dayu-water-agent`
- Main baseline: `57f402b0bd8d24175767b1ca97f2a66e4a5634f3`
- Candidate branch: `phase/agent-phase-01-runtime`
- Candidate head at audit start: `d0101cf855c6ea7d2ff0ab37781a08e9aa83c81a`
- RC1 runtime/CI fix: `cfaaa9e06bdb0785cbdbb7c39f23a578ef0ba350`
- Scope: release-candidate hardening only. No Phase-02 tool-selection, approval, MCP, RAG,
  hydraulic, GIS, or Tiangong integration was added.

## 2. Remote branch state

At audit start, local and remote candidate heads were both `d0101cf`; `main` remained at
`57f402b`. The worktree was clean, no newer remote commit existed, and the audit did not reset,
rewrite, force-push, delete history, or write directly to `main`.

## 3. Local validation review

Windows validation used Python 3.12.13 and a project-local virtual environment. The final real
PostgreSQL run produced 78 passed, 1 opt-in OpenAI test skipped, and 86.47% coverage. Ruff,
strict mypy (46 source files), OpenAPI synchronization, pip check, and compileall passed.

A preliminary run without `DAYU_TEST_DATABASE_URL` correctly skipped eight PostgreSQL tests and
therefore reached only 78.14% coverage; it was not accepted as the release gate. The suite was
rerun against disposable PostgreSQL 17.11 and passed the 80% gate.

## 4. Hosted CI failure

Hosted run [`33701653916`](https://github.com/zj1310426307-stack/dayu-water-agent/actions/runs/33701653916)
failed at commit `d0101cf`. Install, PostgreSQL startup, migrations, Ruff, mypy, and OpenAPI had
already succeeded. The only failure was
`test_production_requires_explicit_postgres_store_and_url`: 1 failed, 73 passed, 1 skipped.

## 5. Root cause

`DATABASE_URL` was defined for the entire CI job. Pydantic `BaseSettings` still reads process
environment variables when `_env_file=None`, so the supposedly isolated unit test received the
runner's PostgreSQL URL and did not raise the expected validation error. This was test isolation
and configuration precedence leakage, not a production validator defect.

## 6. Fix

1. An autouse fixture removes every Dayu runtime settings alias before each test and clears the
   cached process settings before and after the test.
2. A regression test asserts the four known contaminating variables are absent at test entry.
3. CI deliberately sets those four variables for a focused config-test step, proving the fixture
   defeats ambient pollution.
4. `DATABASE_URL` is scoped only to migration verification; `DAYU_TEST_DATABASE_URL` remains
   available to PostgreSQL integration tests.
5. Workflow permissions are `contents: read`.
6. Official stable `actions/checkout@v7` and `actions/setup-python@v7` are used. The final hosted
   runner is `2.337.0`; no `ACTIONS_ALLOW_USE_UNSECURE_NODE_VERSION` escape hatch exists.

## 7. Runtime regression audit

All seven frozen Phase-01 invariants remain intact:

- success-only conversation commit is atomic with run completion and its terminal event;
- identical idempotent requests share one run and one provider execution;
- changed payload under the same key is rejected with `IDEMPOTENCY_CONFLICT` / HTTP 409;
- one active run per session remains fail-fast `SESSION_BUSY`, without an implicit queue;
- cancellation commits terminal state then cancels and awaits the owned `asyncio.Task`;
- Runtime remains the sole retry-budget owner and the OpenAI SDK keeps retries disabled;
- streaming remains run-bound, durable, monotonically sequenced, finite-retention,
  `Last-Event-ID` resumable, and explicitly at-least-once.

Provider failure, guardrail rejection, cancellation, and interruption write no committed turn.
No RC1 production runtime code needed alteration after the independent review.

## 8. Database consistency audit

`SQLAlchemyRuntimeStore` keeps reservation, state transition, message commit, and terminal event
changes inside explicit transactions. Session and run rows serialize the decisive operations;
unique constraints enforce global idempotency keys, one active PostgreSQL run per session, message
sequence, and event sequence. Reservation rechecks idempotency after the decisive session lock and
retains an `IntegrityError` authority path for races. Success commits the paired messages, session
version, completed state, usage/result, and completion event in one transaction.

Alembic upgrade, downgrade to base, and re-upgrade passed on PostgreSQL 17.11. No check-then-act or
post-commit critical mutation defect was found.

## 9. Concurrency audit

Real PostgreSQL tests cover identical-key arbitration, conflicting keys, same-session exclusion,
different-session concurrency, and 50 independent sessions. Row-lock scope is per decisive
session/run rather than global. One active-run partial unique index remains the database backstop.

## 10. Cancellation audit

Cancellation is durable before task signalling, idempotent on repeats, and refuses ownership from
another process. Completion-versus-cancel and retry-backoff-versus-cancel regressions produce one
terminal state and one authoritative terminal event. Shutdown first interrupts owned active rows,
then cancels and awaits local tasks. Terminal-to-nonterminal transitions remain forbidden.

## 11. Retry audit

Attempts are durably incremented before provider invocation. Count, elapsed-time, backoff, and
jitter limits are owned by `RetryBudget`; terminal provider errors are not retried. Any persisted
stream delta disables retry to prevent duplicated output. Cancellation interrupts retry backoff.

## 12. Streaming audit

The store locks the parent run while allocating event sequence and enforces `(run_id, sequence)`
uniqueness. SSE reads committed events only, advances strictly after the cursor, and terminates on
one authoritative terminal event. Real Compose HTTP validation disconnected after event 1 and
resumed with events 2 and 3; the final event was `response.completed`, the run was `completed`,
and the conversation contained the expected four committed messages.

This proves persisted at-least-once resume, not network exactly-once delivery.

## 13. Security audit

- Tiangong imports: 0
- shell tools / subprocess execution surfaces: 0
- generic SQL tools callable by the model: 0
- Python exec/eval tools: 0
- arbitrary filesystem-write tools: 0
- real API keys, tokens, private keys, or key files: 0

The only built-ins are deterministic `system.health` and `system.echo`, both read-only and
schema/permission/timeout guarded. SQLAlchemy access is internal runtime persistence, not an LLM
tool. Safe error shaping and secret-free readiness summaries remain covered by tests.

## 14. Cross-platform findings

Windows Python 3.12.13 and GitHub-hosted Ubuntu 24.04 / Python 3.12.14 both pass. Hosted run
[`33704659182`](https://github.com/zj1310426307-stack/dayu-water-agent/actions/runs/33704659182)
completed every job step successfully with 78 passed, 1 skipped, and 86.59% coverage. The updated
Actions majors use current official runtimes and the hosted log contains no insecure Node override.

Official references: [checkout v7](https://github.com/actions/checkout/releases/tag/v7.0.1) and
[setup-python v7](https://github.com/actions/setup-python/releases/tag/v7.0.0).

## 15. Remaining limitations

OpenAI live remains `NOT VERIFIED` because no paid-call opt-in and credential were supplied.
Cancellation ownership is process-local; replay retention is finite; delivery is at-least-once;
ambiguous external-provider completion is interrupted rather than automatically replayed. There is
no distributed queue, multi-worker cancellation bus, HA guarantee, production IAM, or water-domain
agent in this release candidate.

## 16. Merge recommendation

All RC1 release gates have passed locally and on a real hosted Linux run. The branch is suitable
for a pull request into `main`. Merge must remain a human/repository-policy decision; this audit
does not authorize direct `main` writes, automatic merge, tag creation, or a GitHub Release.

## 17. Supervisor Responsibility Audit

The current `SupervisorAgent` legitimately owns Phase-01 orchestration: admission, context and
session lookup, idempotent run reservation, provider/tool dispatch, retry/cancellation ownership,
success-only commit, and durable stream coordination. It should continue to coordinate those
services rather than absorb their future internals.

Phase-02 should introduce a separate `ToolExecutionService` boundary containing `ToolRegistry`,
`ToolCallRepository`, `ApprovalService`, and `PermissionPolicy`. Model tool selection, approval
persistence, human decision APIs, execution, and tool audit must not be added directly to the
Supervisor. RC1 records this boundary only; none of those Phase-02 objects were implemented.

## 18. Branch protection recommendation

Repository administrators should protect `main` with pull-request-only changes, the CI check as a
required status, and force-push disabled. No repository administration setting was changed during
this task.
