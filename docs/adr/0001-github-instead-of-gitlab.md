# ADR 0001 — Host on GitHub (Actions + PRs) instead of GitLab

- **Status:** Accepted
- **Date:** 2026-07-28
- **Context tasks:** T-01, T-02, T-09

## Context

`PLAN.md` and `ARCHITECTURE.md` were written assuming GitLab as the source of
truth: branches + **Merge Requests**, **GitLab CI** (`.gitlab-ci.yml`), and
`python-gitlab` for group discovery. This repository, however, lives on
**GitHub** (`mmeshari440-tech/taproot`).

`PLAN.md` §0 explicitly sanctions this swap:

> If you genuinely need GitHub, swap `python-gitlab` for `PyGithub` and MR→PR;
> everything else is identical. Don't run both.

## Decision

Host the platform repository on GitHub:

1. **CI** is GitHub Actions (`.github/workflows/ci.yml`) instead of
   `.gitlab-ci.yml`. Path-filtered jobs, lint/typecheck/test/build stages, and
   `import-linter` enforcement are preserved 1:1 (the T-02 acceptance criteria
   are CI-tool-agnostic).
2. **Code review** happens via Pull Requests. Task workflow language ("open an
   MR") maps to "open a PR".
3. The **monitored** repositories' hosting is a **separate** decision. Taproot's
   GitLab *integration* client (T-09) — group discovery and `get_file()` — is
   about the systems Taproot observes, and is unchanged by where this platform's
   own code lives. If the monitored estate is GitLab, the GitLab client stays; if
   GitHub, T-09 uses the GitHub API. This ADR only concerns platform hosting.

## Consequences

- `TASKS.md`/`PLAN.md` still say "GitLab CI" / "Merge Request" in places; treat
  those as "the CI system" / "the review request". They are not rewritten
  wholesale to avoid a noisy diff; this ADR is the pointer.
- Docker images are built in the `images` job on `main` (was: GitLab registry).
- No functional capability is lost; the change is hosting + CI syntax only.
