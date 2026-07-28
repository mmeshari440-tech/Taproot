.DEFAULT_GOAL := help
API := apps/api
WEB := apps/web
DEV_ENV := TAPROOT_DATABASE_URL=sqlite+aiosqlite:///:memory: TAPROOT_LOCAL_SECRET_KEY=dev

.PHONY: help
help: ## List available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

.PHONY: install
install: ## Install all dependencies (api + web)
	cd $(API) && uv sync
	pnpm install

.PHONY: dev
dev: ## Start the full stack (postgres, redis, api, web)
	docker compose up --build

.PHONY: db
db: ## Start only Postgres + Redis
	docker compose up -d postgres redis

.PHONY: down
down: ## Stop the stack
	docker compose down

.PHONY: test
test: ## Run both test suites
	cd $(API) && uv run pytest
	pnpm --filter @taproot/web test

.PHONY: lint
lint: ## ruff + mypy + import-linter + eslint + tsc
	cd $(API) && uv run ruff check . && uv run mypy src && uv run lint-imports
	pnpm --filter @taproot/web lint
	pnpm --filter @taproot/web typecheck

.PHONY: format
format: ## Auto-format Python
	cd $(API) && uv run ruff check . --fix && uv run ruff format .

.PHONY: migrate
migrate: ## Apply migrations to head
	cd $(API) && uv run alembic upgrade head

.PHONY: migrate-down
migrate-down: ## Revert the last migration
	cd $(API) && uv run alembic downgrade -1

.PHONY: revision
revision: ## Autogenerate a migration: make revision m="message"
	cd $(API) && uv run alembic revision --autogenerate -m "$(m)"

.PHONY: seed
seed: ## Seed a demo project + user
	cd $(API) && uv run python -m taproot.db.seed

.PHONY: gen
gen: ## Export the OpenAPI schema to packages/contracts
	cd $(API) && $(DEV_ENV) uv run python -c "import json, pathlib; from taproot.main import create_app; pathlib.Path('../../packages/contracts/openapi.json').write_text(json.dumps(create_app().openapi(), indent=2))"
	@echo "Wrote packages/contracts/openapi.json (TS type generation wired up in T-11)."
