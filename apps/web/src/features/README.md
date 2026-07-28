# Feature modules

Per `ARCHITECTURE.md` §4, each feature is a self-contained folder:

- `auth/` — oidc-client-ts wiring, `ProtectedRoute`, `useRole()` (T-08)
- `admin/` — project creation, GitLab group picker, integration forms (T-10, T-12)
- `investigate/` — submit form, live step timeline, results view (T-19, T-29)
- `history/` — past investigations (T-30)

Folders are created by the task that first needs them.
