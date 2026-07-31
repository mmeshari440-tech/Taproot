# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

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
