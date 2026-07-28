# taproot-api

FastAPI backend + ARQ worker for Taproot.

```bash
uv sync                       # install deps (incl. dev group)
uv run pytest                 # run the test suite
uv run ruff check .           # lint
uv run mypy src               # type-check (strict)
uv run lint-imports           # dependency-direction contracts
uv run uvicorn taproot.main:app --reload   # serve :8000
```

Layout and dependency rules: see `../../docs/ARCHITECTURE.md` §3.
