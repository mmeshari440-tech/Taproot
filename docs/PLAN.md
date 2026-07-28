# Taproot — Build Plan & Agent Prompt

**Taproot** — *the root that goes straight down.*
AI-powered root-cause investigation across Elasticsearch, Sentry, and AppDynamics, running entirely on-prem.

> **How to use this file:** paste it as the root context for your coding agent (Claude Code / Cursor / Cline). It contains the product spec, the recommended stack, the repo strategy, and a numbered task backlog with acceptance criteria. The agent works one task at a time, one branch + one MR per task.
>
> **Companion files:** `ARCHITECTURE.md` (system design) and `TASKS.md` (live task tracker the agent updates as it works).

## Branding

| Item | Value |
|---|---|
| Product name | **Taproot** |
| Tagline | *Follow the error to its root.* |
| Repo / package name | `taproot` |
| Python package | `taproot` |
| Docker images | `taproot/api`, `taproot/web`, `taproot/worker` |
| Env var prefix | `TAPROOT_` |
| DB name | `taproot` |
| K8s namespace | `taproot` |
| Internal domain (suggested) | `taproot.<your-intranet>` |
| Agent's user-facing persona | "Taproot is investigating…" |

> Changing your mind on the name later costs one find-and-replace of `Taproot`/`taproot` across these three docs. Do it before Task T-01, not after.

---

## 0. Agent operating instructions (read first)

You are a senior full-stack engineer building an on-prem AI error-investigation platform.

**Rules:**
1. Work **one task at a time**, in the order given in Section 7. Do not skip ahead.
2. For each task: create branch `feat/T-<id>-<slug>` → implement → write tests → open a Merge Request → wait for review before starting the next task.
3. Never invent API responses. If an external API contract is unknown, write the client against the documented shape and put a `# TODO: verify against live instance` marker plus a recorded-fixture test.
4. **Never send secrets, tokens, or raw `user_name` values to the LLM.** All prompts pass through the redaction layer (Task T-22).
5. Every agent-visible action must emit a step event (Section 5.3) — the UI streams them live.
6. All config via env vars. No hardcoded URLs, no committed credentials. `.env.example` stays current.
7. Definition of Done for every task: code + unit tests + updated OpenAPI schema (if API changed) + updated `.env.example` + a short entry in `CHANGELOG.md`.

**Note on repo hosting:** your source of truth is **GitLab** (you already have the FE/BE group there). The plan below assumes GitLab — the agent pushes branches and opens **Merge Requests** on GitLab, not GitHub. If you genuinely need GitHub, swap `python-gitlab` for `PyGithub` and MR→PR; everything else is identical. Don't run both.

---

## 1. Product summary

A platform where a technical user pastes an error message, selects a project, and an on-prem AI agent investigates across **Elasticsearch/Kibana**, **Sentry**, and **AppDynamics**, then returns: severity, root cause, the location in the code, suggested fixes, and a 7-day frequency chart — while streaming its reasoning steps live to the UI.

**Two roles (from Keycloak):**
- `platform-admin` — creates projects, links GitLab groups, configures + validates integration IDs.
- `tech-user` — selects a project, runs investigations, views results/history.

---

## 2. Recommended stack

### 2.1 Decision summary

| Layer | Choice | Why |
|---|---|---|
| Frontend | **React 18 + TypeScript + Vite** | Fast, standard, huge ecosystem |
| UI kit | **Tailwind + shadcn/ui** | Ships fast, looks intentional, no design debt |
| Data fetching | **TanStack Query** | Cache + retry + polling for investigation history |
| Live steps | **Server-Sent Events (SSE)** | One-way stream, works through corporate proxies far better than WebSockets |
| Charts | **Recharts** | Simple, enough for the 7-day bar/line chart |
| Auth (FE) | **oidc-client-ts** (Authorization Code + PKCE) | Public client, no secret in browser |
| Backend | **Python 3.12 + FastAPI** | The AI/agent ecosystem is Python-first; async fits the fan-out to 3 external APIs |
| Agent orchestration | **LangGraph** | Explicit state machine = deterministic, resumable, and *natively streams node-by-node steps* — exactly requirement #11 |
| Validation | **Pydantic v2** | Structured LLM output + request validation in one library |
| DB | **PostgreSQL 16** (+ `pgvector` later) | Projects, integrations, investigations, audit |
| Cache / queue / pubsub | **Redis 7** | Job state, SSE fan-out, rate limiting |
| Background jobs | **ARQ** (or Celery if you already run it) | Investigations run 30s–3min; must not block HTTP |
| Secrets | **HashiCorp Vault** (fallback: envelope encryption in Postgres via `cryptography` + a KMS/age key) | Integration tokens must never sit in plaintext |
| LLM serving | **vLLM**, OpenAI-compatible endpoint | Swappable, batches well, production-grade |
| Model (reasoning + code) | **Qwen2.5-Coder-32B-Instruct** or **Qwen3-32B**; fallback **Llama 3.3 70B** if you have 2×A100/H100 | Strong tool-calling + code reasoning at on-prem sizes. *Verify current best before ordering hardware.* |
| Embeddings | **bge-m3** or **nomic-embed-text-v1.5** | For code/incident RAG in Phase 2 |
| Auth (BE) | **Keycloak** via JWT validation (`python-jose` + JWKS cache) | Already in your estate |
| Observability | **OpenTelemetry → your own Elastic** | Dogfood it |
| Packaging | **Docker + docker-compose (dev) / Helm on K8s (prod)** | On-prem friendly |
| CI | **GitLab CI** | Native to your group |

### 2.2 Stack decisions worth defending

- **Why Python backend and not Spring Boot / Node?** Requirement 9–10 is the whole product, and LangGraph + Pydantic structured outputs + the tool-calling ecosystem are meaningfully ahead in Python. If your org mandates Java, use the **hybrid** shape: Spring Boot for CRUD/auth/admin, and a small Python `agent-service` behind an internal HTTP contract. Do not try to build the agent loop in Java unless you have to.
- **Why SSE not WebSockets?** You only stream server→client. SSE survives on-prem reverse proxies and needs no extra infra.
- **Why LangGraph over a plain while-loop?** You need replayable steps, per-node timeouts, partial results on failure, and a UI that shows *which* step is running. A hand-rolled loop gets there but you'll rebuild LangGraph badly.
- **Why vLLM not Ollama?** Ollama is great for laptop dev; use it in `docker-compose.dev`. vLLM for anything shared.

### 2.3 Hardware note (blocking dependency)

A 32B model in FP16 needs ~64GB VRAM; AWQ/GPTQ 4-bit brings it to ~20GB. Minimum viable: **1× A100 80GB** or **2× L40S 48GB**. Confirm this before Sprint 1 — it's the longest lead-time item.

---

## 3. Repo strategy — **monorepo**

**Recommendation: one GitLab repo, monorepo.**

Reasons specific to your situation:
- FE and BE change together on almost every feature (new agent step → new step-type in the UI). Two repos means two MRs, two reviews, and version skew on the SSE event schema.
- One team, one release cadence, one deployment unit.
- **The AI agent works dramatically better with the whole codebase in one context** — cross-cutting refactors, shared types, "where does this API get called" queries.
- Shared contracts live in one place: OpenAPI → generated TS client, so FE types can never drift from BE.

Use **path-based CI rules** so a frontend-only change doesn't run the Python test suite.

Split into multiple repos only if: separate teams own FE and BE, or you need independent release trains. You don't, today.

> Note: your GitLab *group* already has FE and BE as separate projects. That's fine — those are the **applications being monitored**, not this platform. This platform is its own new repo.

### 3.1 Layout

```
taproot/
├── apps/
│   ├── web/                      # React + Vite
│   │   ├── src/
│   │   │   ├── features/{admin,investigate,history,auth}/
│   │   │   ├── components/ui/    # shadcn
│   │   │   ├── lib/{api-client,sse,keycloak}.ts
│   │   │   └── types/generated/  # from OpenAPI — DO NOT EDIT
│   └── api/                      # FastAPI
│       ├── src/app/
│       │   ├── api/v1/{projects,integrations,investigations,admin}.py
│       │   ├── core/{config,security,logging,redaction}.py
│       │   ├── db/{models,session}.py + alembic/
│       │   ├── integrations/     # elastic.py sentry.py appdynamics.py gitlab.py keycloak.py
│       │   ├── agent/
│       │   │   ├── graph.py      # LangGraph definition
│       │   │   ├── state.py
│       │   │   ├── nodes/        # one file per node
│       │   │   ├── tools/        # LLM-callable tool wrappers
│       │   │   ├── prompts/      # versioned .jinja2 files
│       │   │   └── schemas.py    # Pydantic result contract
│       │   └── workers/
│       └── tests/{unit,integration,agent_evals}/
├── packages/
│   └── contracts/                # OpenAPI spec + SSE event JSON schemas
├── infra/
│   ├── docker/  helm/  keycloak/realm-export.json
├── docs/
│   ├── adr/                      # architecture decision records
│   └── runbook.md
├── .gitlab-ci.yml
└── CHANGELOG.md
```

---

## 4. Data model (PostgreSQL)

```
users              id, keycloak_sub, email, display_name, last_login_at
                   -- roles come from the JWT, not stored

projects           id, name, slug, description, gitlab_group_id, gitlab_group_path,
                   created_by, created_at, is_active

project_repos      id, project_id, gitlab_project_id, name, kind(FE|BE|OTHER),
                   default_branch, web_url, last_synced_at
                   -- pulled from GitLab group (req #1)

integrations       id, project_id, kind(ELASTIC|SENTRY|APPDYNAMICS|KEYCLOAK),
                   external_id,            -- sentry project slug / AD app id / elastic index pattern
                   base_url, config JSONB, -- non-secret extras
                   secret_ref,             -- Vault path; NEVER the token itself
                   status(UNVERIFIED|OK|FAILED), last_checked_at, last_error TEXT
                   UNIQUE(project_id, kind)

investigations     id, project_id, created_by, error_text, time_window_days,
                   status(QUEUED|RUNNING|DONE|FAILED|CANCELLED),
                   started_at, finished_at, duration_ms, token_usage JSONB, error TEXT

investigation_steps  id, investigation_id, seq, node, title, status, started_at,
                     finished_at, summary TEXT, payload JSONB   -- redacted evidence

investigation_result id, investigation_id,
                     severity(BLOCKER|HIGH|MEDIUM|LOW), severity_rationale,
                     confidence NUMERIC(3,2),
                     root_cause TEXT, root_cause_evidence JSONB,
                     code_locations JSONB,   -- [{repo, path, line, ref, snippet, why}]
                     suggested_fixes JSONB,  -- [{title, description, diff?, risk, effort}]
                     third_party_involved BOOL, third_party_details JSONB,
                     occurrence_series JSONB -- [{date, count}] for the chart
                     raw_model_output JSONB

audit_log          id, actor_id, action, entity_type, entity_id, meta JSONB, created_at
```

**Retention:** investigations + steps auto-purge after N days (env `INVESTIGATION_RETENTION_DAYS`, default 90). Steps carry log excerpts — treat as sensitive.

---

## 5. Contracts

### 5.1 Core REST endpoints

```
# Admin
POST   /api/v1/projects                          {name, gitlab_group_path}
GET    /api/v1/projects/gitlab-groups?search=    -> discover groups from GitLab
POST   /api/v1/projects/{id}/sync-repos          -> pull FE/BE projects from group
GET    /api/v1/projects/{id}

PUT    /api/v1/projects/{id}/integrations/{kind} {external_id, base_url, token, config}
POST   /api/v1/projects/{id}/integrations/{kind}/test   -> {ok, latency_ms, detail, error}
GET    /api/v1/projects/{id}/integrations

# Tech user
GET    /api/v1/projects                          -> projects the caller can access
POST   /api/v1/investigations                    {project_id, error_text, time_window_days}
GET    /api/v1/investigations/{id}
GET    /api/v1/investigations/{id}/stream        -> SSE
GET    /api/v1/investigations?project_id=&page=  -> history
POST   /api/v1/investigations/{id}/cancel
```

**Rule (req #4 & #5):** an integration is saved with `status=UNVERIFIED` and the test runs immediately. If it fails, the API returns `422` with the provider's actual error message and the UI shows it inline on the field. **Investigations are blocked on any project whose Elastic integration is not `OK`.** Sentry/AppDynamics failing = degrade gracefully, warn, continue.

### 5.2 Agent result schema (what the LLM must produce)

```json
{
  "severity": "BLOCKER|HIGH|MEDIUM|LOW",
  "severity_rationale": "string",
  "confidence": 0.0,
  "root_cause": "markdown string",
  "root_cause_evidence": [
    {"source": "elastic|sentry|appdynamics|code", "ref": "string", "excerpt": "string"}
  ],
  "code_locations": [
    {"repo": "be-payments", "path": "src/svc/payment.py", "line": 142,
     "ref": "a1b2c3d", "snippet": "...", "why": "string"}
  ],
  "suggested_fixes": [
    {"title": "string", "description": "markdown", "diff": "unified diff or null",
     "risk": "LOW|MEDIUM|HIGH", "effort": "S|M|L"}
  ],
  "third_party_involved": true,
  "third_party_details": {"service": "string", "symptom": "string", "evidence": "string"},
  "open_questions": ["string"]
}
```

Enforce with Pydantic + a repair loop (max 2 retries). If the model can't produce valid JSON twice, fail the investigation with a clear message — **do not fabricate a result.**

### 5.3 SSE event schema (requirement #11)

```json
{"type":"step.start","seq":3,"node":"thread_walk","title":"Walking transaction thread","ts":"..."}
{"type":"step.progress","seq":3,"message":"Fetched 47 log lines for txn 8f2a…"}
{"type":"step.finish","seq":3,"status":"ok","summary":"Reconstructed 3 request threads","metrics":{"docs":47,"ms":820}}
{"type":"step.finish","seq":4,"status":"skipped","summary":"AppDynamics not configured"}
{"type":"result","payload":{...}}
{"type":"error","message":"Elastic timeout after 30s","recoverable":false}
{"type":"done"}
```

Every node emits `step.start` and exactly one `step.finish`. Heartbeat comment every 15s to keep proxies from closing the connection. On reconnect, replay from `Last-Event-ID` using the persisted `investigation_steps`.

---

## 6. The agent (requirements 9 & 10) — this is the product

### 6.1 Your ELK schema

Fields given: `transaction_id`, `severity`, `user_name`, `message`, `stack_trace`.
Assumed additions to confirm on day 1: `@timestamp`, `service.name`, `http.status_code`, `url.full`, `span_id`/`parent_id`.

**If `@timestamp` and a service field don't exist, the 7-day chart and cross-service correlation are impossible.** Confirm this before Sprint 2.

### 6.2 LangGraph nodes (each = one visible UI step)

| # | Node | Does |
|---|---|---|
| 1 | `normalize_query` | LLM extracts: exception class, key message tokens, probable service, suggested time window. Builds search variants (exact phrase, fuzzy, tokenized). |
| 2 | `elastic_broad_search` | `message` match + `stack_trace` match over the window, `severity in (ERROR,FATAL,CRITICAL)`. Returns top hits + distinct `transaction_id`s. |
| 3 | `select_threads` | Rank transactions by recency + completeness + distinct users. Pick top 3–5 for deep dive. |
| 4 | `thread_walk` ⭐ | **The deep dive.** For each chosen `transaction_id`: fetch *all* docs (any severity) sorted by `@timestamp` ASC, size up to 500. Reconstructs the full request timeline — INFO lines before the error usually contain the actual trigger. |
| 5 | `third_party_probe` | Scan the thread for outbound-call signatures (external hostnames, `HttpClient`, gateway/timeout/5xx patterns, `SocketTimeout`, `ConnectException`). Classify: our bug vs. third-party degradation. |
| 6 | `sentry_enrich` | Search issues by query → latest event → full stacktrace with in-app frames, release, culprit, tags, `userCount`, `firstSeen`/`lastSeen`. |
| 7 | `appdynamics_enrich` | Business transaction health, error rate, and error snapshots for the window; exit-call breakdown to confirm/deny #5. |
| 8 | `code_locate` ⭐ | Parse frames from `stack_trace` + Sentry. Filter to frames matching org package prefixes. For each: resolve repo from `project_repos`, resolve `ref` from the Sentry release/commit (fallback: default branch), `GET` the file from GitLab, extract ±25 lines. |
| 9 | `occurrence_stats` | Elastic `date_histogram` daily over 7 days on the normalized error signature → the chart data (req 12.1). Also distinct `user_name` count (hashed). |
| 10 | `synthesize` | Single LLM call with all gathered evidence → the JSON schema in 5.2. |
| 11 | `verify` | Second LLM pass: "for each claim, cite the evidence id that supports it; drop unsupported claims; lower confidence." Loop back to `synthesize` once if it strips too much. |

Nodes 5–9 run **in parallel** (`asyncio.gather`) — they're independent. That's the difference between a 30s and a 3min investigation.

### 6.3 Elastic query shapes (starting point)

**Broad search:**
```json
{
  "size": 50,
  "query": {"bool": {
    "must": [{"multi_match": {
        "query": "<error_text>",
        "fields": ["message^3", "stack_trace"],
        "type": "best_fields", "fuzziness": "AUTO"}}],
    "filter": [
      {"terms": {"severity": ["ERROR","FATAL","CRITICAL"]}},
      {"range": {"@timestamp": {"gte": "now-7d"}}}
    ]}},
  "aggs": {"txns": {"terms": {"field": "transaction_id", "size": 20}}},
  "sort": [{"@timestamp": "desc"}]
}
```

**Thread walk (the important one):**
```json
{
  "size": 500,
  "query": {"bool": {"filter": [{"term": {"transaction_id": "<id>"}}]}},
  "sort": [{"@timestamp": "asc"}],
  "_source": ["@timestamp","severity","message","stack_trace","service.name","user_name"]
}
```

**7-day histogram:**
```json
{
  "size": 0,
  "query": {"bool": {"must": [{"match_phrase": {"message": "<signature>"}}],
    "filter": [{"range": {"@timestamp": {"gte": "now-7d"}}}]}},
  "aggs": {
    "per_day": {"date_histogram": {"field": "@timestamp", "calendar_interval": "1d"}},
    "users": {"cardinality": {"field": "user_name"}}
  }
}
```

### 6.4 Severity rubric (req 12.2) — deterministic, not vibes

Compute in code, let the LLM only adjust ±1 level with a written reason:

| Signal | Weight |
|---|---|
| Distinct users affected in 7d | >100 = +3, 10–100 = +2, <10 = +1 |
| Occurrences trending up ≥2× vs prior week | +2 |
| Request fails end-to-end (no successful retry in thread) | +2 |
| Auth / payment / data-loss path touched | +3 |
| Third-party outage with no fallback in our code | +2 |
| Third-party outage *with* working fallback | −1 |
| Only affects a single user / single request | −2 |

Score → `≥8 BLOCKER, 5–7 HIGH, 3–4 MEDIUM, <3 LOW`. Store the score breakdown in `severity_rationale` so it's auditable.

### 6.5 Guardrails

- **Token budget per investigation** (`AGENT_MAX_TOKENS`, default 120k). Summarize each thread before it enters the synthesis prompt — never dump 500 raw log lines into the final call.
- **Redaction before every LLM call:** hash `user_name`, mask emails, JWTs, `Bearer` tokens, credit-card and national-ID patterns, and any string matching known secret prefixes.
- **Hard timeouts:** 30s per external call, 5min per investigation, then return partial results with `status=DONE` + `open_questions`.
- **No writes.** The agent has read-only credentials to Elastic, Sentry, AppDynamics, and GitLab. It suggests diffs; it does not commit to the monitored repos.

---

## 7. Task backlog

> **This is the summary view.** The agent's working copy — with status, checkboxes, per-task file lists and a progress dashboard it updates as it goes — lives in **`TASKS.md`**. Keep them in sync; `TASKS.md` is the source of truth once development starts.

Format: `T-id | title | depends on | acceptance criteria`

### Sprint 0 — Foundations

- **T-01 | Monorepo scaffold** | — | pnpm workspace + uv/poetry; `apps/web` Vite+TS+Tailwind+shadcn boots; `apps/api` FastAPI serves `/health`; root `docker-compose.yml` with postgres+redis; `make dev` runs everything.
- **T-02 | GitLab CI pipeline** | T-01 | Path-filtered jobs: lint (ruff/eslint), typecheck (mypy/tsc), test, build images. Pipeline green on empty repo.
- **T-03 | DB + migrations** | T-01 | SQLAlchemy models for all tables in §4; Alembic initial migration; `make migrate` works; seed script creates one demo project.
- **T-04 | Config & secrets layer** | T-01 | Pydantic `Settings`; `SecretStore` interface with `VaultSecretStore` + `LocalEncryptedSecretStore`; `.env.example` complete; unit tests for both.
- **T-05 | Structured logging + OTel** | T-01 | JSON logs w/ request id + user sub; OTel traces exportable to Elastic; secrets never logged (test asserts this).

### Sprint 1 — Auth & Admin (reqs 1, 2, 3, 4, 5)

- **T-06 | Keycloak realm + docker** | T-01 | `infra/keycloak/realm-export.json` with realm, roles `platform-admin`/`tech-user`, public FE client (PKCE), BE audience. Documented in `docs/runbook.md`.
- **T-07 | BE JWT auth + RBAC** | T-06, T-03 | JWKS-cached validation; `require_role()` dependency; 401 vs 403 distinguished; user upserted on first login; tests with signed fixture tokens.
- **T-08 | FE auth flow** | T-06 | oidc-client-ts login/logout/silent-refresh; protected routes; role-aware nav; token attached by API client; 401 → re-auth.
- **T-09 | GitLab client + group discovery** (req 1) | T-04 | `GET /projects/gitlab-groups?search=` lists groups; `sync-repos` pulls group projects and classifies FE/BE by name/topic heuristic with manual override; retries + rate-limit handling.
- **T-10 | Project CRUD + admin UI** | T-07, T-09 | Admin can create a project from a GitLab group; repo list shown with editable FE/BE kind; audit-logged.
- **T-11 | Integration config API + connection tests** (reqs 2, 4) | T-04, T-10 | `PUT` + `POST /test` per kind. Test implementations: Elastic → `_search size:0` on the index pattern; Sentry → `GET /projects/{org}/{proj}/`; AppDynamics → OAuth token then `GET /rest/applications/{id}`; GitLab → `GET /projects/{id}`. Returns `{ok, latency_ms, detail, error}`. Token stored via `SecretStore`, never returned in any response.
- **T-12 | Integration config UI** (reqs 2, 4) | T-11, T-08 | Form per integration with inline "Test connection" and live status badge (green/red/grey). Failure shows the provider's real error text. Save blocked until tested. Existing tokens shown masked with "replace" affordance.

### Sprint 2 — Investigation pipeline (reqs 6, 7, 8, 11)

- **T-13 | Elastic client** | T-11 | Typed wrapper: `search()`, `thread(txn_id)`, `histogram()`, `cardinality()`. Retries, 30s timeout, index-pattern from integration config. Tests against fixtures + a docker Elastic in CI (optional job).
- **T-14 | Sentry client** | T-11 | `search_issues()`, `latest_event()`, `issue_tags()`. Normalizes frames to a common `Frame` model.
- **T-15 | AppDynamics client** | T-11 | OAuth token flow w/ caching; `business_transactions()`, `error_snapshots()`, `metric_data()`. Normalizes to common models.
- **T-16 | LLM client** | T-04 | OpenAI-compatible client pointed at vLLM; streaming; tool/function calling; token accounting; `LLM_BASE_URL`/`LLM_MODEL` env; a `FakeLLM` for tests.
- **T-17 | Investigation API + worker** | T-13, T-03 | `POST /investigations` enqueues to ARQ, returns `202` + id; worker updates status; cancel works; history endpoint paginated and scoped to caller's projects.
- **T-18 | SSE streaming** (req 11) | T-17 | `GET /investigations/{id}/stream` publishes via Redis pub/sub; heartbeat; `Last-Event-ID` replay from DB; reconnect tested.
- **T-19 | FE investigation screen** (reqs 6–8, 11) | T-18, T-08 | Project selector (only projects with healthy Elastic), error textarea, time-window picker, Submit. Live step timeline: pending/running/ok/failed/skipped, elapsed time per step, expandable step detail. Cancel button. Survives page refresh.

### Sprint 3 — The agent (reqs 9, 10)

- **T-20 | LangGraph skeleton + state** | T-16, T-17 | `InvestigationState` model; graph with all 11 nodes wired; each node emits step events; parallel branch for nodes 5–9; per-node timeout + failure isolation (one failing node ≠ failed investigation).
- **T-21 | Nodes 1–4: normalize, broad search, select threads, thread_walk** | T-20, T-13 | Given a seeded Elastic index, produces ≥1 reconstructed thread with correct chronological ordering. This is the core — write the most tests here.
- **T-22 | Redaction layer** | T-20 | `redact()` applied at every LLM boundary. Hashes `user_name`, masks emails/tokens/PII. Test asserts no raw secret or username reaches `FakeLLM`.
- **T-23 | Node 5: third-party probe** | T-21 | Detects external-call failure patterns; sets `third_party_involved` + evidence. Fixture tests for: gateway timeout, connection refused, 429 from partner, and a clean internal NPE (must be `false`).
- **T-24 | Nodes 6–7: Sentry + AppDynamics enrichment** | T-14, T-15, T-20 | Both run in parallel, degrade to `skipped` with a UI-visible reason when unconfigured or failing.
- **T-25 | Node 8: code_locate** (req 12.4) | T-09, T-20 | Stack frames → org-package filter → repo resolution → GitLab file fetch at resolved `ref` → ±25-line snippet. Handles: missing file, wrong ref (falls back to default branch), minified/vendor frames (skipped). Returns ≤5 ranked locations.
- **T-26 | Node 9: occurrence_stats** (req 12.1) | T-13 | Daily buckets over 7d + distinct affected users + week-over-week delta.
- **T-27 | Nodes 10–11: synthesize + verify** | T-22, T-25, T-26 | Versioned Jinja prompts; Pydantic-validated output with 2-retry repair; verify pass drops uncited claims and sets confidence.
- **T-28 | Deterministic severity scorer** (req 12.2) | T-26, T-23 | Implements §6.4 in pure Python + unit tests per signal. LLM may shift one level with a recorded reason.

### Sprint 4 — Results & hardening (req 12)

- **T-29 | Results UI** (reqs 12.1–12.5) | T-19, T-27 | Sections: severity badge + rationale + confidence; 7-day Recharts bar chart w/ WoW delta; root cause (markdown) with expandable evidence cards linking to Kibana/Sentry/AppD; code locations with syntax-highlighted snippet + "open in GitLab"; suggested fixes as cards with risk/effort and copyable diff; third-party banner when applicable; open questions.
- **T-30 | Investigation history** | T-17, T-29 | Per-project list, filter by severity/date, re-open past result, "re-run" button.
- **T-31 | Agent eval harness** | T-27 | `tests/agent_evals/`: ≥10 golden incidents with known root causes. Asserts severity within ±1 level and correct file identified in top-3. Runs nightly in CI, reports a scorecard.
- **T-32 | Rate limits, quotas, cost guards** | T-17 | Per-user concurrent investigation cap; global GPU concurrency semaphore; token budget enforced; friendly 429s.
- **T-33 | Security pass** | all | Read-only creds documented; secrets audit; OWASP headers; CORS locked; SSE authorized per-investigation; pen-test checklist in `docs/`.
- **T-34 | Deployment** | T-02 | Helm chart (api, worker, web, redis, postgres, optional vLLM); readiness/liveness probes; migration job; `docs/runbook.md` with rollback.

### Phase 2 (backlog — don't build yet)

- Code RAG over the FE/BE repos with pgvector, so `code_locate` works even without a clean stack trace.
- Agent-authored fix MRs on the monitored repos (behind an explicit admin toggle + mandatory human review).
- Slack/Teams alerting on new BLOCKER investigations.
- Feedback loop: thumbs up/down on results → eval set growth.
- Proactive mode: agent watches Elastic for new error signatures and investigates unprompted.

---

## 8. Open questions — answer these before Sprint 2

1. Does your ELK index have `@timestamp` and a service/application field? **Blocks the chart and cross-service correlation.**
2. What is the index pattern per project — one shared index with a service filter, or one index per app?
3. Is `transaction_id` propagated across FE→BE→third-party calls, or is it per-service?
4. Sentry: self-hosted or SaaS? Which org slug? Are releases tagged with git SHAs? (**T-25 quality depends on this.**)
5. AppDynamics controller URL + do you have API-client credentials (OAuth) or only basic auth?
6. Keycloak realm name, and are FE and BE separate clients today?
7. GPU availability — see §2.3. Longest lead time.
8. Do you need on-prem air-gap, or can the platform reach the internet for package installs?

---

## 9. Suggested sequencing

| Sprint | Focus | Demo-able outcome |
|---|---|---|
| 0 | Foundations | CI green, app boots, DB migrates |
| 1 | Auth + Admin | Admin creates a project from GitLab, configures + validates all 3 integrations |
| 2 | Pipeline | Tech user submits an error, sees fake steps stream live |
| 3 | Agent | Real investigation returns a real root cause |
| 4 | Results + hardening | Full result view, evals passing, deployed to staging |

Sprint 3 is where the risk lives. Budget for it to take twice as long as Sprint 2.
