# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

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
