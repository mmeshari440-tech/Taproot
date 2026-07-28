# Taproot

*Follow the error to its root.*

On-prem, AI-powered root-cause investigation across **Elasticsearch**, **Sentry**,
and **AppDynamics**. A technical user pastes an error, picks a project, and a local
LLM agent investigates — returning severity, root cause, the location in code,
suggested fixes, and a 7-day frequency chart, streaming its reasoning live. Nothing
leaves the network.

> **Docs:** [`docs/PLAN.md`](docs/PLAN.md) (product + stack) ·
> [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (system design) ·
> [`docs/TASKS.md`](docs/TASKS.md) (live task tracker) · [`CLAUDE.md`](CLAUDE.md)
> (agent working rules).

## Quick start (< 10 commands)

```bash
git clone <this-repo> && cd taproot
cp .env.example .env
make install        # uv sync (api) + pnpm install (web)
make db             # postgres + redis via docker compose
make migrate        # alembic upgrade head
make seed           # demo project + user
make dev            # full stack: api :8000, web :5173
```

Verify: `curl localhost:8000/health` → `{"status":"ok","version":"0.1.0"}`.

## Repository layout

```
apps/
  api/    FastAPI backend + ARQ worker (Python 3.12, mypy --strict)
  web/    React + TypeScript + Vite + Tailwind + shadcn/ui
packages/
  contracts/   OpenAPI + SSE event schemas (shared)
infra/    docker / helm / keycloak
docs/     PLAN · ARCHITECTURE · TASKS · adr/ · runbook
```

Backend module boundaries (`api → services → {db, integrations, agent} → core`)
are enforced by `import-linter` in CI — see `docs/ARCHITECTURE.md` §3.

## Common commands

| Command | Does |
|---|---|
| `make dev` | Run the full stack |
| `make test` | Both test suites (pytest + vitest) |
| `make lint` | ruff + mypy --strict + import-linter + eslint + tsc |
| `make migrate` | Apply DB migrations |
| `make gen` | Export the OpenAPI schema |

## Status

**Sprint 0 — Foundations** (T-01…T-05): monorepo scaffold, CI, database +
migrations, config + secrets, structured logging + OpenTelemetry. See
`docs/TASKS.md` for the live tracker.

Hosting note: this platform runs on GitHub Actions + PRs rather than the GitLab
assumed in the planning docs — see [`docs/adr/0001`](docs/adr/0001-github-instead-of-gitlab.md).
