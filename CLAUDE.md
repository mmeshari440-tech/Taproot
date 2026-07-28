# Taproot

AI-powered root-cause investigation across Elasticsearch, Sentry, and AppDynamics. Fully on-prem.

## Read these before doing anything

| File | What it is | When to read it |
|---|---|---|
| `docs/TASKS.md` | **Live task tracker — source of truth for progress** | Before starting *any* task, and update it after every task |
| `docs/ARCHITECTURE.md` | Authoritative system design | Before writing code in any area it covers |
| `docs/PLAN.md` | Product spec, stack rationale, requirements | For "why is it built this way" and full requirement text |

## How you work

1. Open `docs/TASKS.md`. Find the first `TODO` task whose dependencies are all `DONE`.
2. Mark it `IN_PROGRESS`. Create branch `feat/T-<id>-<slug>`.
3. Re-read the relevant `docs/ARCHITECTURE.md` section — **do not work from memory**.
4. Implement. Write tests. Tick every box in the task's Definition of Done.
5. Open a Merge Request on GitLab. Mark the task `REVIEW`. Update the dashboard counters.
6. **Stop and wait for human review.** Do not start the next task.

Only one task may be `IN_PROGRESS` at a time.

## Non-negotiable rules

- **Never invent API responses.** If an external contract is unknown, code to the documented shape, add `# TODO: verify against live instance`, and write a fixture test.
- **Never send secrets, tokens, or raw `user_name` values to the LLM.** Everything goes through `core/redaction.py` first.
- **Never fabricate an investigation result.** If evidence is insufficient, say so and fail loudly.
- **Never mark a task `DONE` to keep momentum.** A falsely-done task corrupts every downstream decision.
- **Never silently diverge from `ARCHITECTURE.md`.** If reality contradicts it, write an ADR in `docs/adr/`, note it on the task, and raise it.
- **Read-only credentials only.** Taproot never writes to Elastic, Sentry, AppDynamics, or the monitored repos. It proposes diffs; humans apply them.
- If you get stuck: mark the task `BLOCKED`, add a row to the Blockers table with what you need and from whom, move to the next eligible task. If none is eligible, stop and report.

## Commands

```bash
make dev        # docker-compose up: postgres, redis, keycloak, api, web
make test       # full suite, both apps
make lint       # ruff + mypy --strict + eslint + tsc + import-linter
make migrate    # alembic upgrade head
make gen        # regenerate OpenAPI schema -> TS types
```

## Conventions

- Python 3.12, async-first, `mypy --strict`, Pydantic at every boundary, no bare `except`.
- TypeScript strict, no `any`. No `fetch` outside `lib/api-client.ts`. Never hand-edit `types/generated/`.
- Commits: Conventional Commits, scoped, task-tagged — `feat(agent): add thread_walk node [T-21]`.
- Dependency direction is enforced by `import-linter` in CI. See `docs/ARCHITECTURE.md` §3.

## Current status

Pre-Sprint 0. Nothing implemented. Next task: **T-01 — Monorepo scaffold**.
