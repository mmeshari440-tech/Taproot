# ADR 0002 — Integrations are per-application, not per-project

- **Status:** Proposed (awaiting decision)
- **Date:** 2026-08-01
- **Affects:** data model (`integrations`), T-11/T-12 (merged), T-21, T-24, T-25, T-26

## Context

`ARCHITECTURE.md` §7 and PLAN §4 model **one integration per (project, kind)** —
e.g. a single Elastic index pattern per project — assuming a shared index with a
`service.name` field and a `transaction_id` that propagates across service
boundaries (§12 assumptions 2 & 3).

The operator's answers to the open questions (TASKS.md, 2026-08-01) contradict
this:

| # | Answer | Consequence |
|---|---|---|
| 1 | `@timestamp` exists | ✅ matches the plan |
| 2 | Each **application** has its own ES namespace/index and its own Sentry account; the in-log service field is **`container.name`** (not `service.name`) | Integrations are per-app; field mapping changes |
| 3 | `transaction_id` propagates **backend-only** | `thread_walk` reconstructs backend threads; FE↔BE cannot be joined by `transaction_id` |
| 4 | **One index per app** | No single per-project index pattern |
| 5 | Self-hosted Sentry, **one account per app (FE, BE)** | One Sentry config per app, not per project |

A Taproot **project** groups a GitLab group's apps (`project_repos`: FE/BE/…).
Reality is therefore: **each app (repo) has its own Elastic index and Sentry
account.** The per-project single-integration model cannot represent this.

## Decision (proposed — needs sign-off)

Move integration configuration from **per `(project, kind)`** to **per
`(project_repo, kind)`** — i.e. configure an Elastic index + Sentry account per
**application**, keyed off the repos already synced from GitLab (T-09).

Consequences:
- **Schema:** `integrations.project_id` → `integrations.project_repo_id` (new
  migration; `project_repos` already exists). `UNIQUE(project_repo, kind)`.
- **API/UI (T-11/T-12, merged):** integration config + connection-test endpoints
  and the admin UI move under a repo, not a project. Rework required.
- **Agent (T-21+):** `thread_walk` runs against the relevant app's index; the
  Elastic client maps the service field from **`container.name`**; the
  investigation submit flow (T-17) associates an error with an app (or the agent
  fans out across the project's apps).
- **T-24 (Sentry):** one Sentry client per app account.
- **Gate:** "investigations blocked unless Elastic is OK" becomes per-app.

`transaction_id` (Q3) being backend-only is **compatible** with `thread_walk`
(the deep dive is inherently backend log reconstruction); we simply document that
FE↔BE correlation is not available via `transaction_id` (a Phase-2 concern, e.g.
trace/span ids or source maps).

## Alternatives considered

1. **Per-repo integrations (recommended above).** Cleanest fit; costs a
   migration + T-11/T-12 rework.
2. **Keep per-project, store per-app entries in `config` JSON** (list of
   `{app, index_pattern}` / `{app, sentry_account}`). Smaller schema change; the
   connection-test + agent iterate over entries. Messier; weaker validation.
3. **Redefine a Taproot "project" as a single application.** Simplest data model
   (per-project == per-app), but drops the "group FE+BE under one project" concept
   from PLAN §1 and changes repo-sync semantics.

## Status / ask

Blocking T-21 until the model is chosen. Recommendation: **Option 1
(per-repo integrations)**. Raised with the maintainer 2026-08-01.
