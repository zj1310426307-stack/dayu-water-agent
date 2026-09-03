STATUS: PASS

Repository: `https://github.com/zj1310426307-stack/dayu-water-agent`

Branch: `phase/agent-phase-01-runtime`

Baseline Commit: `57f402b0bd8d24175767b1ca97f2a66e4a5634f3`

Final Runtime Commit: `cfaaa9e06bdb0785cbdbb7c39f23a578ef0ba350` (RC1 implementation validated by Hosted CI; documentation closure follows)

Implemented: PostgreSQL sessions/runs/messages/events; atomic success-only history; idempotency;
same-session exclusion; task cancellation; retry budget; durable resumable SSE; reconciliation;
readiness; graceful shutdown; metrics; optional OpenTelemetry; API/CLI/Compose/CI/OpenAPI/docs.

Tests: Windows/PostgreSQL 78 passed, 1 opt-in OpenAI live test skipped, coverage 86.47%;
Linux Hosted CI 78 passed, 1 skipped, coverage 86.59%.

Verification: Ruff PASS; strict mypy PASS; OpenAPI check PASS; pytest/coverage PASS; pip check
PASS; compileall PASS; Alembic up/down/up PASS on real PostgreSQL 17.11; 50-session concurrency
PASS; API/SSE/restart persistence PASS.

Hosted CI Run ID: `33704659182`

Hosted CI Commit SHA: `cfaaa9e06bdb0785cbdbb7c39f23a578ef0ba350`

Hosted CI Conclusion: `success` (Ubuntu 24.04, Python 3.12.14, PostgreSQL 17.11)

Docker: PASS — final image built; API and PostgreSQL healthy; migration `0002`; API version 0.2.0;
non-root process; persisted state survived API restart/recreation.

OpenAI Live: NOT VERIFIED — no explicit paid-call opt-in and credential supplied; not a core gate.

Security: PASS — Tiangong imports 0; shell tools 0; SQL tools 0; Python exec tools 0; arbitrary
filesystem write tools 0; real secrets 0; safe errors and bounded execution retained.

Changed Paths: `src/dayu_agent/{contracts,runtime,memory,database,agents,providers,api,config,observability}`;
`alembic/versions/0002_phase01_runtime.py`; tests; Docker/Compose; CI; OpenAPI generator/artifact;
README; ADR-0004–0007; Phase-01 validation/report/status.

RC1 Closure: Hermetic settings fixture added; `DATABASE_URL` restricted to the migration step;
an explicit polluted-environment regression gate added; `actions/checkout` and
`actions/setup-python` upgraded to v7 with read-only repository permissions. Initial Hosted CI
failure `33701653916` is retained in the validation history.

Known Limitations: process-local cancellation ownership; finite at-least-once stream replay;
single active run per session; ambiguous remote provider completion is interrupted, not replayed;
failed-attempt usage can be underreported by SDKs; no distributed queue/IAM/HA/domain agents.

Not Verified: Real OpenAI paid model call.

Recommended Next Phase: AGENT-PHASE-02 Tool Runtime & Permissions, preserving all Phase-01
transaction, idempotency, cancellation, and stream contracts.
