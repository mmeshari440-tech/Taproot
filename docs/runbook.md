# Taproot Runbook

Operational notes. Grows as sprints land (Keycloak T-06, deploy T-34).

## Local development

Prerequisites: Docker, `uv`, Node 22 + `pnpm`.

```bash
cp .env.example .env          # 1. configure
make install                  # 2. install api + web deps
make db                       # 3. start postgres + redis
make migrate                  # 4. apply migrations
make seed                     # 5. seed a demo project + user
make dev                      # 6. run the full stack (or run api/web individually)
```

The API serves `http://localhost:8000` (`GET /health`), the web app
`http://localhost:5173`.

## Common tasks

| Task | Command |
|---|---|
| Run all tests | `make test` |
| Lint + type-check everything | `make lint` |
| New DB migration | `make revision m="add X"` then review the generated file |
| Roll a migration back | `make migrate-down` |
| Export OpenAPI schema | `make gen` |

## Troubleshooting

- **`ConfigurationError: Missing required configuration: TAPROOT_DATABASE_URL`** —
  the required var is unset. Copy `.env.example` to `.env` or export it.
- **Migrations fail to connect** — ensure `make db` is up and healthy
  (`docker compose ps`).

## Keycloak (T-06)

`make dev` starts Keycloak at `http://localhost:8080` and auto-imports
`infra/keycloak/realm-export.json` (`start-dev --import-realm`).

| Item | Value |
|---|---|
| Realm | `taproot` |
| Admin console | `http://localhost:8080` (admin / admin) |
| Realm roles | `platform-admin`, `tech-user` |
| SPA client | `taproot-web` — public, PKCE `S256`, redirect `http://localhost:5173/*` |
| API audience | `taproot-api` — access tokens carry `aud: taproot-api` via an audience mapper on `taproot-web` |
| OIDC discovery | `http://localhost:8080/realms/taproot/.well-known/openid-configuration` |
| JWKS | `http://localhost:8080/realms/taproot/protocol/openid-connect/certs` |

Test users (dev passwords — change for any shared environment):

| Username | Password | Role |
|---|---|---|
| `admin@taproot.local` | `admin` | `platform-admin` |
| `tech@taproot.local` | `tech` | `tech-user` |

The backend validates tokens against this realm — see `TAPROOT_KEYCLOAK_*` in
`.env.example` and `docs/ARCHITECTURE.md` §8.1.

## Coming in later sprints

- Vault setup for integration secrets (T-11).
- Helm deploy, SSE ingress config, rollback procedure (T-34).
