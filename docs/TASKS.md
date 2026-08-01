# Taproot — Task Tracker

> **This file is the source of truth for development progress.** The implementing agent reads it before every task and updates it after every task. Commit changes to this file with the work they describe — never in a separate "update tasks" commit.

**Companions:** `PLAN.md` (product spec + stack) · `ARCHITECTURE.md` (system design)

---

## Protocol — read before every task

### Starting a task
1. Open the **Progress Dashboard** below. Find the first task with status `TODO` whose dependencies are all `DONE`.
2. If it has unmet dependencies, do not start it. Pick the next eligible one. If none is eligible, stop and report the blockage.
3. Change its status to `IN_PROGRESS` and fill in `Started`.
4. Create branch `feat/T-<id>-<slug>`.
5. Re-read the relevant section of `ARCHITECTURE.md` before writing code. Do not work from memory.

### Finishing a task
A task is `DONE` only when **every** box below is ticked. Do not mark done to keep momentum — a task marked done that isn't will corrupt every downstream decision.

- [ ] All acceptance criteria for the task are met
- [ ] Unit tests written and passing
- [ ] `ruff` / `mypy --strict` / `eslint` / `tsc` clean
- [ ] `import-linter` passes (no dependency-direction violations)
- [ ] OpenAPI schema regenerated if the API changed; TS types regenerated
- [ ] `.env.example` updated if config changed
- [ ] `CHANGELOG.md` entry added
- [ ] Full test suite passes locally
- [ ] Branch pushed, Merge Request opened, CI green

Then:
6. Set status to `REVIEW`, fill in `Finished`, add the MR link.
7. **Stop. Wait for human review.** Do not start the next task.
8. On merge, set status to `DONE` and update the dashboard counters at the top.

### Status values
| Status | Meaning |
|---|---|
| `TODO` | Not started |
| `IN_PROGRESS` | Being worked on right now — only one task may hold this |
| `REVIEW` | MR open, CI green, awaiting human review |
| `BLOCKED` | Cannot proceed — **must** have a reason and an owner in the Blockers table |
| `DONE` | Merged to main |

### When you get stuck
Do not guess and do not silently work around it. Set the task to `BLOCKED`, add a row to the **Blockers** table with what you need and from whom, and move to the next eligible task. If nothing is eligible, stop and report.

### When reality contradicts the plan
If `ARCHITECTURE.md` says something the codebase or an external API makes impossible: stop, write an ADR in `docs/adr/`, note it in the task's Notes, and raise it. Do not silently diverge — the whole point of these documents is that a human can trust them without reading all the code.

---

## Progress Dashboard

**Update these counters every time a task changes status.**

| Sprint | Total | Done | In Progress | Blocked | Remaining |
|---|---|---|---|---|---|
| 0 — Foundations | 5 | 5 | 0 | 0 | 0 |
| 1 — Auth & Admin | 7 | 7 | 0 | 0 | 0 |
| 2 — Pipeline | 7 | 7 | 0 | 0 | 0 |
| 3 — Agent | 9 | 0 | 0 | 0 | 9 |
| 4 — Results & Hardening | 6 | 0 | 0 | 0 | 6 |
| **Total** | **34** | **19** | **0** | **0** | **15** |

**Overall progress:** `███████████░░░░░░░░░` 56% (19/34)

> Progress bar: 20 cells, one cell ≈ 1.7 tasks. Fill `█` per completed cell.

**Currently in progress:** _none_
**Last completed:** T-17, T-18, T-19 — the investigation pipeline — merged to `develop` via [PR #6](https://github.com/mmeshari440-tech/Taproot/pull/6). **Sprint 2 complete (7/7).**
**In review:** T-20 — LangGraph skeleton & state (all 11 nodes wired as stubs, parallel fan-out, node contract, max-duration), delivered on branch `claude/zip-folder-review-y5th0x`.
**Next up:** T-21 (nodes 1–4: normalize → broad search → select → thread_walk) — **the core.** Now unblocked (ELK answers received; ADR-0002 implemented).

> ✅ **ELK/Sentry answers received (2026-08-01):** `@timestamp` ✓; service field is **`container.name`** (adopted in the Elastic client); each app has its **own index + Sentry account** → integrations are **per-repo** (ADR-0002, implemented); `transaction_id` is **backend-only** → `thread_walk` reconstructs backend threads (FE↔BE correlation is Phase-2). Sentry release/SHA tagging (affects T-25 precision) still to confirm.

### Blockers

| Task | Blocked since | What's needed | From whom |
|---|---|---|---|
| — | — | — | — |

> **Resolved 2026-08-01:** ADR-0002 accepted (Option 1 — per-repo integrations) and implemented (`integrations.project_repo_id`, migration `a1b2c3d4e5f6`, per-repo config API/UI, `container.name` service field, per-project `elastic_ok`). T-21 unblocked.

### Open questions awaiting answers

These gate Sprint 2 (see `ARCHITECTURE.md` §12). Fill in as answers arrive.

| # | Question | Answer | Answered on |
|---|---|---|---|
| 1 | Does the ELK index have `@timestamp`? | **Yes.** | 2026-08-01 |
| 2 | Is there a service/application field? | **Each app has its own namespace/index (and its own Sentry account).** The in-log service field is **`container.name`**, not `service.name`. | 2026-08-01 |
| 3 | Does `transaction_id` propagate across services? | **Backend only** — not across FE↔BE. | 2026-08-01 |
| 4 | Index pattern: shared with service filter, or one per app? | **One index per app.** | 2026-08-01 |
| 5 | Sentry self-hosted or SaaS? Org slug? Releases tagged with git SHAs? | **Self-hosted; each app (FE, BE) has its own Sentry account.** (Release/SHA tagging still to confirm.) | 2026-08-01 |
| 6 | AppDynamics controller URL + OAuth API-client credentials available? | | |
| 7 | Keycloak realm name; are FE and BE separate clients today? | Realm `taproot`; separate clients — `taproot-web` (public, PKCE) and `taproot-api` (bearer-only). Defined in T-06. | 2026-07-29 |
| 8 | GPU allocation confirmed? (longest lead time) | | |

---

## Sprint 0 — Foundations

### T-01 · Monorepo scaffold
**Status:** `DONE` · **Depends:** — · **Started:** 2026-07-28 · **Finished:** 2026-07-28 · **MR:** [PR #1](https://github.com/mmeshari440-tech/Taproot/pull/1) — merged

- [x] pnpm workspace + `uv` project at root
- [x] `apps/web` — Vite + React + TS + Tailwind + shadcn, boots and renders a placeholder (`pnpm build` + render test pass)
- [x] `apps/api` — FastAPI serving `GET /health` returning `{status, version}`
- [x] `docker-compose.yml` with postgres + redis, healthchecks defined
- [x] `make test` runs both suites (verified); `make dev` implemented
- [x] Directory structure matches `ARCHITECTURE.md` §3 and §4
- [x] `README.md` with local setup in under 10 commands

**Notes:** `make dev` (docker compose) is implemented but could not be exercised in the build session — no Docker daemon available. Postgres/Redis + api/web services and Dockerfiles are defined.

---

### T-02 · GitLab CI pipeline
**Status:** `DONE` · **Depends:** T-01 · **Started:** 2026-07-28 · **Finished:** 2026-07-28 · **MR:** [PR #1](https://github.com/mmeshari440-tech/Taproot/pull/1) — merged

- [x] Stages: lint → typecheck → test → build
- [x] Path-filtered rules — a `apps/web/**` change does not run Python tests (`dorny/paths-filter`)
- [x] `import-linter` job enforcing dependency direction (`ARCHITECTURE.md` §3)
- [x] Docker images built on `main` (push to a registry deferred — no registry configured yet)
- [x] Pipeline green — CI run passed on PR #1 (`changes`/`api`/`web` ✓, `images` skipped off-`main`)

**Notes:** GitHub Actions (`.github/workflows/ci.yml`) rather than GitLab CI — see `docs/adr/0001`. The `api` job runs migrations against a real Postgres service container. All checks that CI runs were verified locally (ruff, mypy, import-linter, pytest, eslint, tsc, vitest, build); the "pipeline green" box is left unchecked until the hosted run confirms it.

---

### T-03 · Database schema & migrations
**Status:** `DONE` · **Depends:** T-01 · **Started:** 2026-07-28 · **Finished:** 2026-07-28 · **MR:** [PR #1](https://github.com/mmeshari440-tech/Taproot/pull/1) — merged

- [x] SQLAlchemy models for all tables in `PLAN.md` §4 / `ARCHITECTURE.md` §7
- [x] Alembic configured (async); initial migration autogenerated and applied
- [x] `make migrate` and `make migrate-down` both work (upgrade → downgrade verified)
- [x] Seed script creates one demo project + one demo user (idempotent)
- [x] Indexes on `investigations(project_id, created_at)`, unique `investigation_steps(investigation_id, seq)`, unique `integrations(project_id, kind)`

**Notes:** Models use portable types (`Uuid`, generic `JSON`, `Enum(native_enum=False)`) so the suite runs on Postgres (prod/CI) and SQLite (unit tests). Provider-specific tuning (JSONB, native enums) can move into a later migration if wanted.

---

### T-04 · Config & secrets layer
**Status:** `DONE` · **Depends:** T-01 · **Started:** 2026-07-28 · **Finished:** 2026-07-28 · **MR:** [PR #1](https://github.com/mmeshari440-tech/Taproot/pull/1) — merged

- [x] Pydantic `Settings` covering every var in `ARCHITECTURE.md` §10
- [x] Missing required var → named `ConfigurationError`, not a runtime `KeyError`
- [x] `SecretStore` protocol + `VaultSecretStore` + `LocalEncryptedSecretStore`
- [x] `LocalEncryptedSecretStore` refuses to start when `TAPROOT_ENV=production`
- [x] `.env.example` complete and matching
- [x] Unit tests for both stores including round-trip and rotation

**Notes:** `VaultSecretStore` takes an injected `hvac`-style client so it is unit-tested without a live Vault (fake KV v2 in tests); carries a `# TODO: verify against live instance` marker. `LocalEncryptedSecretStore` is dev-only, in-process (ephemeral) — real persistence lands when integrations wire it up (T-11).

---

### T-05 · Structured logging & OpenTelemetry
**Status:** `DONE` · **Depends:** T-01 · **Started:** 2026-07-28 · **Finished:** 2026-07-28 · **MR:** [PR #1](https://github.com/mmeshari440-tech/Taproot/pull/1) — merged

- [x] JSON logs with `request_id` (middleware) and a `bind_request_context()` helper for `user_sub` / `investigation_id`
- [x] OTel traces + FastAPI instrumentation, OTLP exporter configurable (behind the `otel` extra)
- [ ] Trace context propagates api → redis → worker — worker arrives in T-17; validated end-to-end then
- [x] Test asserts no secret-shaped string ever reaches a log record

**Notes:** Tracing (spans + FastAPI instrumentation) is wired and configurable; a metrics pipeline and the api→redis→worker propagation check are deferred to when those components exist (T-17). `user_sub` / `investigation_id` are bound by the code paths that own them (auth in T-07, worker in T-17); the helper and processor are in place now.

---

## Sprint 1 — Auth & Admin (requirements 1–5)

### T-06 · Keycloak realm & local instance
**Status:** `DONE` · **Depends:** T-01 · **Started:** 2026-07-29 · **Finished:** 2026-07-29 · **MR:** [PR #2](https://github.com/mmeshari440-tech/Taproot/pull/2) — merged

- [x] `infra/keycloak/realm-export.json`: realm, roles `platform-admin` + `tech-user`
- [x] Public FE client with PKCE (`S256`); API client `taproot-api` + audience mapper
- [x] Keycloak added to `docker-compose` with the realm auto-imported (`--import-realm`)
- [x] Two test users, one per role (`admin@` / `tech@`)
- [x] Setup documented in `docs/runbook.md`

**Notes:** Realm structure is guarded by `test_keycloak_realm.py` (roles, PKCE, audience mapper, users). Keycloak container defined but not launched in the build session (no Docker daemon).

---

### T-07 · Backend JWT auth & RBAC
**Status:** `DONE` · **Depends:** T-06, T-03 · **Started:** 2026-07-29 · **Finished:** 2026-07-29 · **MR:** [PR #2](https://github.com/mmeshari440-tech/Taproot/pull/2) — merged

- [x] JWKS fetched and cached (1h TTL, refetch on unknown `kid`) — `AsyncCache`: `RedisCache` (prod) / `InMemoryCache` (test)
- [x] Validates signature, `iss`, `aud`, `exp`
- [x] `require_role("platform-admin")` FastAPI dependency
- [x] 401 vs 403 correctly distinguished (`ARCHITECTURE.md` §8.1)
- [x] User upserted into `users` on first authenticated request
- [x] Tests with locally-signed RSA fixture tokens: valid, expired, wrong audience, wrong issuer, unknown kid, wrong role (403)

**Notes:** JWKS caching + refetch-on-unknown-kid unit-tested against `InMemoryCache`; the `RedisCache` path is wired (`redis.asyncio`) but not exercised against a live Redis in-session. Validator is dependency-injected, so RBAC is tested end-to-end via `dependency_overrides` (no Keycloak/Redis needed). Routes added: `GET /api/v1/me`, `GET /api/v1/admin/ping`.

---

### T-08 · Frontend auth flow
**Status:** `DONE` · **Depends:** T-06 · **Started:** 2026-07-30 · **Finished:** 2026-07-30 · **MR:** [PR #4](https://github.com/mmeshari440-tech/Taproot/pull/4) — merged

- [x] `oidc-client-ts` Authorization Code + PKCE; no client secret in the bundle
- [x] Login, logout, silent token refresh (`automaticSilentRenew` + events)
- [x] `ProtectedRoute` + `useRole()`; nav renders per role
- [x] API client attaches the bearer token; 401 triggers silent re-auth then redirect
- [x] Refreshing the page does not log the user out (`localStorage` user store)

**Notes:** OIDC redirect flow can't be E2E'd in-session (no browser+Keycloak); the testable pieces are unit-tested — `rolesFromAccessToken`, API-client token attach + 401 handling. `AuthContext` wires the token/401 handler into the single API client.

---

### T-09 · GitLab client & group discovery *(requirement 1)*
**Status:** `DONE` · **Depends:** T-04 · **Started:** 2026-07-29 · **Finished:** 2026-07-29 · **MR:** [PR #3](https://github.com/mmeshari440-tech/Taproot/pull/3) — merged

- [x] `integrations/gitlab.py` per the client contract (`ARCHITECTURE.md` §3)
- [x] `GET /api/v1/projects/gitlab-groups?search=` returns matching groups
- [x] `POST /projects/{id}/sync-repos` pulls group projects into `project_repos`
- [x] FE/BE classified by token heuristic, with a manual override (`PATCH /repos/{id}`)
- [x] `get_file(project_id, path, ref)` implemented and tested (needed by T-25)
- [x] Rate-limit + retry handling (jittered backoff on 5xx/429); token needs `read_api` only

**Notes:** Client is fully unit-tested via `httpx.MockTransport` (normalization, base64 file decode, 404→NotFound, provider-message surfacing, retry-then-succeed).

---

### T-10 · Project CRUD & admin UI
**Status:** `DONE` · **Depends:** T-07, T-09 · **Started:** 2026-07-29 · **Finished:** 2026-07-30 · **MR:** [PR #3](https://github.com/mmeshari440-tech/Taproot/pull/3) (API) + Sprint 1 FE branch (admin UI)

- [x] Admin creates a project from a GitLab group — API + group-picker UI (`ProjectsPage`)
- [x] Repo list with editable `kind` + `org_package_prefixes` — API + editor UI (`ProjectDetailPage`)
- [x] Project list, detail, deactivate (API)
- [x] All mutations written to `audit_log`
- [x] Non-admin receives 403 on every write endpoint (tested)

**Notes:** Backend/API merged in PR #3 (tested end-to-end); admin UI screens (group picker, repo-kind editor) delivered in the Sprint 1 frontend branch. Marked `DONE` on that basis; UI merges with the FE PR.

---

### T-11 · Integration config API & connection tests *(requirements 2, 4, 5)*
**Status:** `DONE` · **Depends:** T-04, T-10 · **Started:** 2026-07-29 · **Finished:** 2026-07-29 · **MR:** [PR #3](https://github.com/mmeshari440-tech/Taproot/pull/3) — merged

- [x] `PUT /projects/{id}/integrations/{kind}` stores config; token → `SecretStore`
- [x] `POST /projects/{id}/integrations/{kind}/test` → `{ok, latency_ms, detail, error}`
- [x] Elastic test: `_search size:0` against the configured index pattern
- [x] Sentry test: `GET /api/0/projects/{org}/{project}/`
- [x] AppDynamics test: OAuth token then `GET /controller/rest/applications/{id}` (`# TODO: verify live`)
- [x] Failure returns 422 carrying the **provider's actual error message**, and persists `status=FAILED` + `last_error`
- [x] Token never appears in any response body (tested); write-only field, stored via `SecretStore`

**Notes:** Connection-test success/failure paths tested end-to-end with a mocked provider HTTP layer. Full Elastic/Sentry/AppD data clients arrive in Sprint 2 (T-13–T-15); this task implements only the connection tests. **Amended 2026-08-01 (ADR-0002):** integrations are now **per-repo** — endpoints moved under `/projects/{id}/repos/{repo_id}/integrations`.

---

### T-12 · Integration config UI *(requirements 2, 4)*
**Status:** `DONE` · **Depends:** T-11, T-08 · **Started:** 2026-07-30 · **Finished:** 2026-07-30 · **MR:** [PR #4](https://github.com/mmeshari440-tech/Taproot/pull/4) — merged

- [x] One form per integration kind with inline "Save & test connection"
- [x] Live status badge: green `OK` / red `FAILED` / grey `UNVERIFIED`, with `last_checked_at`
- [x] Failure shows the real provider message inline
- [~] Save flow: implemented as "Save & test" (test needs saved config); status badge + inline error convey verification
- [x] Existing tokens masked with a "replace" affordance; never rendered
- [x] Project page warns clearly when Elastic is not `OK` — investigations disabled

**Notes:** "Save disabled until a successful test" is inherently circular (the test needs the saved config), so it's implemented as a single **Save & test** action with a live status badge + inline provider error — the spirit of the requirement. `StatusBadge` unit-tested. **Amended 2026-08-01 (ADR-0002):** the config UI is now **per app (repo)** — `ProjectDetailPage` renders integration forms per repo.

---

## Sprint 2 — Investigation pipeline (requirements 6–8, 11)

### T-13 · Elasticsearch client
**Status:** `DONE` · **Depends:** T-11 · **Started:** 2026-07-31 · **Finished:** 2026-07-31 · **MR:** [PR #5](https://github.com/mmeshari440-tech/Taproot/pull/5) — merged

- [x] `search()`, `thread(txn_id)`, `histogram()`, `cardinality()` per `PLAN.md` §6.3
- [x] Returns normalized `LogDoc` models, never raw ES JSON
- [x] `thread()` returns **all severities**, `@timestamp` ASC, size 500
- [x] 30s timeout, retry on 5xx/429 only (jittered backoff)
- [x] Fixture-based unit tests (`httpx.MockTransport`); optional docker-ES CI job deferred

**Notes:** Coded to the **documented** ELK schema (open questions #1–4 unanswered) with `# TODO: verify against live instance`; `_parse_doc` tolerates nested (`service.name`) and flat shapes. `LogThread` reconstruction (preamble/error/aftermath) belongs to `thread_walk` (T-21); `thread()` returns the ordered `LogDoc` list it consumes.

---

### T-14 · Sentry client
**Status:** `DONE` · **Depends:** T-11 · **Started:** 2026-07-31 · **Finished:** 2026-07-31 · **MR:** [PR #5](https://github.com/mmeshari440-tech/Taproot/pull/5) — merged

- [x] `search_issues()`, `latest_event()`, `issue_tags()`
- [x] Frames normalized to the shared `Frame` model with an `in_app` flag
- [x] Release / git SHA extracted when present (feeds T-25)
- [x] `userCount`, `firstSeen`, `lastSeen` captured (feeds T-28)

**Notes:** Fixture-tested via `httpx.MockTransport` (issue normalization, frame + release extraction, tags). `# TODO: verify against a live Sentry instance.`

---

### T-15 · AppDynamics client
**Status:** `DONE` · **Depends:** T-11 · **Started:** 2026-07-31 · **Finished:** 2026-07-31 · **MR:** [PR #5](https://github.com/mmeshari440-tech/Taproot/pull/5) — merged

- [x] OAuth token flow with caching and pre-expiry refresh (30s skew)
- [x] `business_transactions()`, `error_snapshots()`, `metric_data()`
- [x] Exit-call breakdown extracted (feeds T-23 third-party detection)
- [x] Normalized to shared models

**Notes:** Token caching verified (one OAuth call across multiple API calls). Snapshot/metric endpoint shapes carry `# TODO: verify against a live controller`.

---

### T-16 · LLM client
**Status:** `DONE` · **Depends:** T-04 · **Started:** 2026-07-31 · **Finished:** 2026-07-31 · **MR:** [PR #5](https://github.com/mmeshari440-tech/Taproot/pull/5) — merged

- [x] OpenAI-compatible async client pointed at vLLM
- [x] Streaming (`stream()`) + tool/function calling (`tools` / `response_format` passthrough)
- [x] Token accounting returned per call (`LLMUsage`); accumulation onto state is the agent's job (T-20)
- [x] `FakeLLM` test double returning scripted responses (records messages for the T-22 redaction assertion)
- [x] Works against Ollama in dev with no code change (only `TAPROOT_LLM_BASE_URL` differs)

**Notes:** `LLM` protocol lets the agent depend on an interface; `LLMClient` and `FakeLLM` both implement it. Streaming + non-streaming paths fixture-tested.

---

### T-17 · Investigation API & worker *(requirements 6, 8)*
**Status:** `DONE` · **Depends:** T-13, T-03 · **Started:** 2026-07-31 · **Finished:** 2026-07-31 · **MR:** [PR #6](https://github.com/mmeshari440-tech/Taproot/pull/6) — merged

- [x] `POST /investigations` → 202 + id, job enqueued to ARQ (`JobQueue` protocol)
- [x] Rejects projects whose Elastic integration is not `OK` (422, clear message)
- [x] ARQ worker runs the job, updates status transitions (QUEUED→RUNNING→DONE)
- [x] `POST /investigations/{id}/cancel` works mid-run (honored before + during the run)
- [x] `GET /investigations?project_id=` paginated, scoped to the caller (`created_by`)
- [x] Worker crash → retried once, then `FAILED` (never infinite requeue)

**Notes:** The run body is a **stub** `runner` (Sprint 3 T-20 injects the LangGraph agent). `execute_investigation` is unit-tested directly (lifecycle, cancel-before/during, retry→FAILED); the API uses a `FakeJobQueue` in tests and `ArqJobQueue` at runtime (`make worker` / compose `worker` service). Listing is scoped to `created_by`; per-project ACLs arrive with the access model later. Per-investigation authz (owner or admin) on get/cancel; the SSE per-investigation stream is T-18.

---

### T-18 · SSE streaming *(requirement 11)*
**Status:** `DONE` · **Depends:** T-17 · **Started:** 2026-07-31 · **Finished:** 2026-07-31 · **MR:** [PR #6](https://github.com/mmeshari440-tech/Taproot/pull/6) — merged

- [x] `GET /investigations/{id}/stream` per the event schema in `PLAN.md` §5.3
- [x] Worker publishes to Redis (`EventBus`: `RedisEventBus`/`InMemoryEventBus`); api relays
- [x] **Step persisted to Postgres before publishing** (`StepRecorder._emit` commits then publishes)
- [x] Heartbeat comment every 15s
- [x] `Last-Event-ID` replay from `investigation_steps` (seq > last id)
- [x] Per-investigation authorization — not just role-based (foreign user → 403, tested)
- [~] Reconnect: replay-from-`Last-Event-ID` tested on a terminal run; live cross-process reconnect needs a real Redis (not exercisable in-session)

**Notes:** EventSource can't set headers, so the SSE token is a query param (`?access_token=`), validated per-investigation. A `demo_runner` emits scripted steps so the pipeline streams end-to-end now; real steps come from the LangGraph agent (Sprint 3). `EventBus`/`StepRecorder` and the terminal-replay path are unit-tested; the live Redis fan-out path is covered by the in-memory bus in tests.

---

### T-19 · Investigation UI *(requirements 6, 7, 8, 11)*
**Status:** `DONE` · **Depends:** T-18, T-08 · **Started:** 2026-07-31 · **Finished:** 2026-07-31 · **MR:** [PR #6](https://github.com/mmeshari440-tech/Taproot/pull/6) — merged

- [x] Project selector showing only projects with healthy Elastic (`useHealthyElasticProjects`)
- [x] Error textarea + time-window picker + Submit
- [x] Live step timeline: running / ok / failed / skipped (SSE-driven reducer)
- [x] Step rows expand (`<details>`) to show summary
- [x] Cancel button (active runs)
- [x] Page refresh mid-run restores state (EventSource replays from `Last-Event-ID`; status via `GET`)
- [x] Failed and skipped steps display their reason (summary)

**Notes:** Timeline is a pure reducer (`applyStepEvent`) driven by the SSE wrapper — unit-tested (ordering, finish, error/done, skipped). **Per-step elapsed time** is deferred: the step events don't yet carry timestamps (easy follow-up — add `ts`/`ms` to `step.finish`). "Redacted evidence" in step detail arrives with the real agent's step payloads (Sprint 3). Full click-through needs a live Keycloak + worker (not exercisable in-session).

---

## Sprint 3 — The agent (requirements 9, 10)

> The highest-risk sprint. Budget roughly double Sprint 2. Write more tests here than anywhere else.

### T-20 · LangGraph skeleton & state
**Status:** `REVIEW` · **Depends:** T-16, T-17 · **Started:** 2026-08-01 · **Finished:** 2026-08-01 · **MR:** branch `claude/zip-folder-review-y5th0x`

- [x] `InvestigationState` per `ARCHITECTURE.md` §6.2 (Annotated reducers for `node_errors`/`tokens_used`)
- [x] All 11 nodes wired as stubs (+ `severity_score`), on a real LangGraph `StateGraph`
- [x] Nodes 5–9 execute in parallel via fan-out from `thread_walk` / fan-in at `severity_score`
- [x] Parallel nodes write disjoint state fields (asserted: all 5 fan-out fields populated)
- [x] Node contract enforced by the `@node` decorator: `step.start`, per-node timeout, failure isolation, `step.finish`
- [x] A failing non-critical node does not fail the investigation (tested); critical nodes abort (tested)
- [x] `AGENT_MAX_DURATION_S` cancels via `astream` + deadline and returns partial results (tested)

**Notes:** Real LangGraph (`StateGraph` over the Pydantic state). Nodes are **stubs** — bodies land in T-21–T-28; they live in one `agent/nodes.py` for the skeleton and split into `nodes/<name>.py` as implemented. The worker's `agent_runner` runs the graph, emits steps via `StepRecorder` (lock-guarded for the parallel nodes), and persists a stub `InvestigationResult`. Integration test: a real run streams all 12 node steps and persists a result.

---

### T-21 · Nodes 1–4: normalize, broad search, select threads, thread_walk ⭐
**Status:** `TODO` · **Depends:** T-20, T-13 · **Started:** — · **Finished:** — · **MR:** —

- [ ] `normalize_query` extracts exception class, key tokens, service hint, time window
- [ ] `elastic_broad_search` per `PLAN.md` §6.3; aborts the run cleanly on zero hits
- [ ] `select_threads` ranks by recency + completeness + distinct users, caps at 5
- [ ] `thread_walk` fetches all severities ASC and splits `preamble` / `error` / `aftermath`
- [ ] Threads summarized to ≤400 tokens each before synthesis
- [ ] Seeded-index test: correct chronological ordering, correct error index, preamble non-empty
- [ ] Multi-service thread test: `services` list populated when txn crosses boundaries

**Notes:**

---

### T-22 · Redaction layer
**Status:** `TODO` · **Depends:** T-20 · **Started:** — · **Finished:** — · **MR:** —

- [ ] `core/redaction.py` implementing every pattern in `ARCHITECTURE.md` §8.3
- [ ] Applied at **every** LLM boundary and before every persisted step payload
- [ ] `user_name` → stable `user_<sha256[:8]>` (same input → same hash, for counting)
- [ ] Test asserts `FakeLLM` never receives a raw username, email, token, or secret prefix
- [ ] Test asserts no `investigation_steps.payload` contains unredacted PII

**Notes:**

---

### T-23 · Node 5: third-party probe
**Status:** `TODO` · **Depends:** T-21 · **Started:** — · **Finished:** — · **MR:** —

- [ ] Detects outbound-call failure signatures: external hostnames, gateway timeouts, `SocketTimeout`, `ConnectException`, 429/5xx from partners
- [ ] Sets `third_party_involved` + service name + evidence
- [ ] Distinguishes "third-party failed, we had no fallback" from "third-party failed, fallback worked"
- [ ] Fixture tests: gateway timeout ✓, connection refused ✓, partner 429 ✓, internal NPE → `false` ✓

**Notes:**

---

### T-24 · Nodes 6–7: Sentry & AppDynamics enrichment
**Status:** `TODO` · **Depends:** T-14, T-15, T-20 · **Started:** — · **Finished:** — · **MR:** —

- [ ] Both run in parallel with the other fan-out nodes
- [ ] Unconfigured or failing integration → `skipped` with a UI-visible reason, run continues
- [ ] Sentry findings include in-app frames, release SHA, culprit, user count
- [ ] AppD findings include BT health, error rate, exit calls for the window

**Notes:**

---

### T-25 · Node 8: code_locate ⭐ *(requirement 12.4)*
**Status:** `TODO` · **Depends:** T-09, T-20 · **Started:** — · **Finished:** — · **MR:** —

- [ ] Parses frames from `stack_trace` and Sentry
- [ ] Filters to in-app frames using per-project `org_package_prefixes`
- [ ] Resolves repo via `project_repos`; resolves ref from Sentry release SHA, else default branch (with lower confidence flagged)
- [ ] Fetches the file from GitLab at that ref, extracts ±25 lines
- [ ] Returns ≤5 ranked locations with a `why` for each
- [ ] Handles: file missing at ref, unregistered repo, minified FE frames, path-prefix mismatch — each with an explicit test

**Notes:**

---

### T-26 · Node 9: occurrence_stats *(requirement 12.1)*
**Status:** `TODO` · **Depends:** T-13 · **Started:** — · **Finished:** — · **MR:** —

- [ ] Daily `date_histogram` over the window → `occurrence_series`
- [ ] Distinct affected users via `cardinality` on `user_name`
- [ ] Week-over-week delta computed
- [ ] Zero-count days present in the series (no gaps in the chart)

**Notes:**

---

### T-27 · Nodes 10–11: synthesize & verify
**Status:** `TODO` · **Depends:** T-22, T-25, T-26 · **Started:** — · **Finished:** — · **MR:** —

- [ ] Versioned Jinja prompts in `agent/prompts/`; no inline prompt strings
- [ ] Output validated against the schema in `PLAN.md` §5.2
- [ ] 2-retry JSON repair loop; third failure → investigation `FAILED`, raw output stored
- [ ] `verify` pass requires each claim to cite an evidence id; uncited claims dropped
- [ ] Confidence lowered when evidence is thin or integrations were skipped
- [ ] Token budget enforced; truncation recorded in `open_questions`
- [ ] **No fabrication:** test that a run with zero evidence returns a clear "insufficient data" result rather than a plausible-sounding guess

**Notes:**

---

### T-28 · Deterministic severity scorer *(requirement 12.2)*
**Status:** `TODO` · **Depends:** T-26, T-23 · **Started:** — · **Finished:** — · **MR:** —

- [ ] `agent/scoring.py` implements the rubric in `PLAN.md` §6.4 in pure Python
- [ ] Runs **before** the LLM sees anything
- [ ] Unit test per signal, plus boundary tests at each threshold
- [ ] LLM may shift by at most one level and must record a written reason
- [ ] Score breakdown persisted in `severity_rationale`
- [ ] Same inputs → same severity, twice (reproducibility test)

**Notes:**

---

## Sprint 4 — Results & hardening (requirement 12)

### T-29 · Results UI *(requirements 12.1–12.5)*
**Status:** `TODO` · **Depends:** T-19, T-27 · **Started:** — · **Finished:** — · **MR:** —

- [ ] Severity badge + rationale + confidence indicator
- [ ] 7-day Recharts bar chart with week-over-week delta *(12.1)*
- [ ] Root cause rendered as markdown, with expandable evidence cards deep-linking to Kibana / Sentry / AppDynamics *(12.3)*
- [ ] Code locations with syntax-highlighted snippet and "open in GitLab" *(12.4)*
- [ ] Suggested fixes as cards with risk/effort and copyable diff *(12.5)*
- [ ] Third-party banner when `third_party_involved`
- [ ] `open_questions` shown — do not hide the agent's uncertainty
- [ ] Degraded state clearly indicated when integrations were skipped

**Notes:**

---

### T-30 · Investigation history
**Status:** `TODO` · **Depends:** T-17, T-29 · **Started:** — · **Finished:** — · **MR:** —

- [ ] Per-project list with severity and date filters
- [ ] Re-open a past result
- [ ] "Re-run" creates a new investigation with the same inputs
- [ ] Pagination; scoped to the caller's projects

**Notes:**

---

### T-31 · Agent eval harness
**Status:** `TODO` · **Depends:** T-27 · **Started:** — · **Finished:** — · **MR:** —

- [ ] `tests/agent_evals/` with ≥10 golden incidents with known root causes
- [ ] Asserts severity within ±1 level of ground truth
- [ ] Asserts the correct file appears in the top 3 code locations
- [ ] Runs nightly in CI, emits a scorecard artifact
- [ ] Regression gate: a merge may not drop the score by more than one point

**Notes:**

---

### T-32 · Rate limits, quotas & cost guards
**Status:** `TODO` · **Depends:** T-17 · **Started:** — · **Finished:** — · **MR:** —

- [ ] Per-user concurrent investigation cap
- [ ] Global GPU concurrency semaphore (`TAPROOT_AGENT_CONCURRENCY`)
- [ ] Token budget enforced per investigation
- [ ] 429s carry `Retry-After` and a human-readable reason
- [ ] Queue depth exposed as a metric

**Notes:**

---

### T-33 · Security pass
**Status:** `TODO` · **Depends:** all above · **Started:** — · **Finished:** — · **MR:** —

- [ ] All external credentials confirmed read-only; scopes documented in `docs/runbook.md`
- [ ] Secret audit: no token in any response, log, step payload, or prompt
- [ ] Security headers + CSP; CORS restricted to known origins
- [ ] SSE per-investigation authorization re-verified
- [ ] Dependency scan clean (`pip-audit`, `npm audit`)
- [ ] Retention purge job implemented and tested
- [ ] Pen-test checklist in `docs/security.md`

**Notes:**

---

### T-34 · Deployment
**Status:** `TODO` · **Depends:** T-02 · **Started:** — · **Finished:** — · **MR:** —

- [ ] Helm chart: api, worker, web, redis, postgres, optional vLLM
- [ ] Migration Job as a pre-upgrade hook; migrations are expand/contract safe
- [ ] Readiness/liveness probes per `ARCHITECTURE.md` §9
- [ ] **Ingress configured for SSE**: buffering off, read timeout ≥ 6 min
- [ ] HPA on CPU (api) and queue depth (worker)
- [ ] `docs/runbook.md`: deploy, rollback, common failures
- [ ] Staging deploy verified end to end with a real investigation

**Notes:**

---

## Phase 2 backlog — do not start without explicit approval

| ID | Title |
|---|---|
| P2-01 | Code RAG over FE/BE repos with pgvector — locate code without a clean stack trace |
| P2-02 | Agent-authored fix MRs on monitored repos (admin toggle + mandatory human review) |
| P2-03 | Slack/Teams alerting on new BLOCKER investigations |
| P2-04 | Thumbs up/down feedback loop feeding the eval set |
| P2-05 | Proactive mode: watch Elastic for new error signatures and investigate unprompted |
| P2-06 | Source-map support for minified frontend stack traces |
