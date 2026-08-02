# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### ADR-0002 — Per-application integrations

- Integrations moved from **per-project** to **per-repo/app** (each app has its own
  Elastic index + Sentry account). `integrations.project_id` → `project_repo_id`
  (migration `a1b2c3d4e5f6`); config + connection-test API/UI now live under
  `/projects/{id}/repos/{repo_id}/integrations`.
- Elastic client maps the service field from **`container.name`** (falls back to
  `service.name`).
- `elastic_ok` exposed per project (any app verified); the investigation gate and
  the investigate project selector use it. `thread_walk` documented as backend-only
  (`transaction_id` doesn't cross FE↔BE).

### Sprint 3 — The agent (deep dive)

- **T-23 Node 5: third-party probe**: deterministic classification of our-bug vs.
  third-party degradation. Scans the walked threads (or `broad_hits` as fallback)
  for outbound-call failure signatures — client/timeout exceptions (`SocketTimeout`,
  `ConnectException`, `UnknownHostException`, SSL handshake, …), partner HTTP
  statuses (429/502/503/504 from the normalized `http_status` field or explicitly
  labelled in text), and gateway phrases — while a bare internal `NullPointerException`
  stays `involved=false`. Sets `third_party_involved` + service (external hostname
  when present) + evidence, and detects whether a fallback/circuit-breaker/cache in
  the aftermath absorbed the failure (swings severity per PLAN.md §7).
- **T-22 Redaction layer**: extended the T-05 secret scrubber into the full
  `ARCHITECTURE.md` §8.3 pipeline — `redact_text` (secrets → email local-part mask
  → credit-card/national-ID PII), `redact_value` (recursive JSON scrub that hashes
  `user_name`-like keys), `hash_user` (stable `user_<sha256[:8]>`, now the single
  source `thread_walk` uses too), and `redact_messages`. The LLM boundary is a
  `RedactingLLM` wrapper around the `LLM` protocol, so the agent never holds a raw
  model and there is no bypass. `StepRecorder.finish` redacts the summary and any
  payload **before** persisting *and* publishing (SSE), keeping raw PII out of
  `investigation_steps` and the browser. Tests assert `FakeLLM` never receives a raw
  username/email/token/secret and that persisted step payloads are redacted.
- **T-21 Nodes 1–4 (normalize → broad search → select → thread_walk)**: the core
  investigation path is now real. `normalize_query` deterministically extracts the
  exception class, key tokens, a service hint, and search variants. The agent reads
  Elasticsearch through an `ElasticSearcher` **port** (`agent/context.py`, satisfied
  by `integrations.elastic.ElasticClient`); the worker injects **one client per
  verified app** (ADR-0002) via `integration_service.elastic_clients_for_project`,
  keeping `agent` free of `db`/`integrations`. `elastic_broad_search` merges +
  de-dups candidate transactions across apps, aborts only when *all* apps error,
  and routes a zero-hit run straight to synthesis via a new conditional edge (clean
  "insufficient evidence" abort). `select_threads` ranks by distinct users →
  completeness → recency and caps at `max_threads`. `thread_walk` fetches all
  severities `@timestamp` ASC, marks `error_index` at the first
  ERROR/FATAL/CRITICAL (preamble/aftermath fall out around it), lists distinct
  services, pseudonymizes the username (`user_<sha256[:8]>`; full redaction is
  T-22), and summarizes each thread to ≤400 tokens.

### Sprint 3 — The agent (skeleton)

- **T-20 LangGraph skeleton & state**: `InvestigationState` (ARCHITECTURE.md §6.2)
  with reducers for `node_errors`/`tokens_used`; a real LangGraph `StateGraph`
  wiring all 11 nodes (+ `severity_score`) as stubs — nodes 1→4 sequential, 5–9
  fan-out/fan-in, then synthesize→verify. `@node` decorator enforces the node
  contract (step.start, per-node timeout, failure isolation, step.finish); the two
  critical nodes abort on failure. `run_graph` streams state and enforces the
  max-duration budget (partial results on deadline). The worker's `agent_runner`
  runs the graph, emits steps via a lock-guarded `StepRecorder`, and persists the
  result. Node bodies are implemented in T-21+.

### Sprint 2 — Investigation pipeline (run engine)

- **T-19 Investigation UI**: submit page (project selector limited to healthy-Elastic
  projects, error textarea, time-window) and a live run view with an SSE-driven step
  timeline (running/ok/failed/skipped, expandable), cancel, and refresh-restore via
  `Last-Event-ID` replay. Pure `applyStepEvent` reducer + typed `EventSource` wrapper.
- **T-18 SSE streaming**: `GET /investigations/{id}/stream` per PLAN.md §5.3;
  `EventBus` (Redis pub/sub in prod, in-memory in tests); `StepRecorder` persists
  each step to Postgres **before** publishing (correct `Last-Event-ID` replay);
  15s heartbeat; per-investigation authz (token via query param since EventSource
  can't set headers). A `demo_runner` emits scripted steps so the pipeline streams
  end-to-end ahead of the real agent (Sprint 3).
- **T-17 Investigation API + ARQ worker**: `POST /investigations` (202 + enqueue),
  gated on a verified Elastic integration (422 otherwise); `GET /investigations`
  (paginated, scoped to the caller); `GET`/`cancel` with per-investigation authz
  (owner or admin). `JobQueue` protocol (`ArqJobQueue` runtime / `FakeJobQueue`
  tests); `workers/tasks.py` drives QUEUED→RUNNING→DONE with cancellation and
  retry-once-then-FAILED semantics, over an injectable `runner` (the LangGraph
  agent is wired in Sprint 3, T-20). `make worker` + compose `worker` service.

### Sprint 2 — Investigation pipeline (external client layer)

- **T-13 Elasticsearch client**: `ElasticClient.search/thread/histogram/cardinality`
  (PLAN.md §6.3) returning normalized `LogDoc` (never raw ES JSON); `thread()` is
  all-severities, `@timestamp` ASC; retry on 5xx/429; coded to the documented ELK
  schema with `# TODO: verify against live instance` + fixture tests.
- **T-14 Sentry client**: `SentryClient.search_issues/latest_event/issue_tags`;
  frames normalized to the shared `Frame` model with `in_app`; release SHA + user
  count captured.
- **T-15 AppDynamics client**: OAuth token flow with caching/pre-expiry refresh;
  `business_transactions/error_snapshots/metric_data`; exit-call breakdown for
  third-party detection.
- **T-16 LLM client**: OpenAI-compatible `LLMClient` (vLLM/Ollama) with streaming,
  tool/`response_format` passthrough, and token accounting; `FakeLLM` test double;
  narrow `LLM` protocol for the agent to depend on.
- Added normalized models to `core/models.py` (LogDoc, Frame, Sentry/AppD/LLM
  types) and a shared `send_with_retries` helper.

### Sprint 1 — Auth & Admin (frontend)

- **T-08 Frontend auth flow**: `oidc-client-ts` Authorization Code + PKCE (public
  client); `AuthProvider`/`useAuth`/`useRole`, `ProtectedRoute`, `/callback`;
  silent renew; the API client attaches the bearer token and re-auths on 401;
  session survives refresh via `localStorage`.
- **T-10 admin UI**: `ProjectsPage` (GitLab group picker → create) and
  `ProjectDetailPage` (repo list with editable kind, sync-from-GitLab) —
  completes T-10 (backend merged in PR #3).
- **T-12 Integration config UI**: per-integration "Save & test" forms with live
  status badge (OK/FAILED/UNVERIFIED + last-checked), inline provider error,
  masked token with a "replace" affordance, and an Elastic-not-OK warning that
  flags investigations as disabled.
- Frontend deps added: `@tanstack/react-query`, `react-router-dom`,
  `oidc-client-ts`; UI primitives (card, input, label, badge). Vitest coverage for
  role extraction, API-client token/401 handling, and status badge.

### Sprint 1 — Auth & Admin (backend)

- **T-09 GitLab client & group discovery**: `integrations/gitlab.py` (groups,
  group projects, `get_file`, `test_connection`) per the client contract —
  normalized models, jittered retry on 5xx/429, structured logging;
  `GET /api/v1/projects/gitlab-groups` and `POST /projects/{id}/sync-repos` with
  token-based FE/BE classification and a `PATCH` manual override.
- **T-10 Project CRUD (API)**: `services/project_service.py` + `/api/v1/projects`
  routes (create from group, list, detail, repos, deactivate), all audit-logged,
  writes gated on `platform-admin`. Admin UI screens land with the FE slice.
- **T-11 Integration config & connection tests**: `services/integration_service.py`
  + `/projects/{id}/integrations` routes; tokens are write-only (stored via
  `SecretStore`, never returned); per-provider tests (Elastic `_search size:0`,
  Sentry project GET, AppDynamics OAuth→app); failures return **422** with the
  provider's real message and persist `status=FAILED`.
- Added normalized domain models (`core/models.py`) and shared integration HTTP
  helpers; `IntegrationError` / `NotFoundError` exceptions.

### Sprint 1 — Auth & Admin (auth backbone)

- **T-06 Keycloak realm & local instance**: `infra/keycloak/realm-export.json`
  (realm `taproot`, roles `platform-admin`/`tech-user`, public PKCE SPA client
  `taproot-web`, bearer-only `taproot-api` with an audience mapper, two test
  users); Keycloak added to `docker-compose` with `--import-realm`; runbook
  section; structural test guarding the export.
- **T-07 Backend JWT auth & RBAC**: `core/security.py` — `TokenValidator`
  (signature + `iss`/`aud`/`exp`), `RemoteJwksProvider` with cached JWKS
  (`AsyncCache`: `RedisCache`/`InMemoryCache`) and refetch-on-unknown-`kid`;
  `require_role()` dependency with strict 401-vs-403; user upsert on first
  authenticated request; routes `GET /api/v1/me` and `GET /api/v1/admin/ping`.
  Tested with locally-signed RSA fixture tokens.

### Sprint 0 — Foundations

- **T-01 Monorepo scaffold**: pnpm workspace + `uv` project; `apps/web`
  (Vite + React + TS + Tailwind + shadcn) boots; `apps/api` (FastAPI) serves
  `GET /health`; `docker-compose.yml` with Postgres + Redis (healthchecks);
  `Makefile` (`make dev`/`test`/`lint`/`migrate`/`seed`/`gen`); directory
  structure per `ARCHITECTURE.md` §3–4.
- **T-02 CI pipeline**: GitHub Actions (`.github/workflows/ci.yml`) with
  path-filtered `api`/`web` jobs — ruff, mypy `--strict`, `import-linter`,
  Alembic-on-Postgres, pytest; eslint, tsc, vitest, build; image build on `main`.
  Hosting decision recorded in `docs/adr/0001`.
- **T-03 Database schema & migrations**: SQLAlchemy models for all tables
  (`PLAN.md` §4); Alembic configured (async); initial migration; portable types
  (Postgres + SQLite); seed script; indexes on `investigations(project_id,
  created_at)`, `investigation_steps(investigation_id, seq)`, unique
  `integrations(project_id, kind)`.
- **T-04 Config & secrets**: Pydantic `Settings` covering `ARCHITECTURE.md` §10;
  missing required var → named `ConfigurationError`; `SecretStore` protocol with
  `VaultSecretStore` (injected client) + `LocalEncryptedSecretStore`
  (refuses production); round-trip + rotation tests.
- **T-05 Structured logging & OpenTelemetry**: structlog JSON logs with
  request-id context; secret-scrubbing processor (test asserts no secret-shaped
  string reaches a record); optional OTel tracing behind the `otel` extra.
