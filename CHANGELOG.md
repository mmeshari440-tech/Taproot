# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Sprint 1 — Auth & Admin (backend, in progress)

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
