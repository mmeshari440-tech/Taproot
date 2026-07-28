# @taproot/contracts

Single source of truth for cross-app contracts (ARCHITECTURE.md §5, PLAN.md §5):

- `openapi.json` — exported from the FastAPI app (`make gen`); drives the
  generated TS client in `apps/web/src/types/generated/`.
- SSE event JSON schemas — the `step.start` / `step.progress` / `step.finish` /
  `result` / `error` / `done` envelope.

Populated in T-11 (OpenAPI) and T-18 (SSE schema).
