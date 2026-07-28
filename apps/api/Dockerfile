FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN pip install --no-cache-dir uv

WORKDIR /app

# Dependency layer (cached until pyproject/lock change).
COPY pyproject.toml README.md ./
COPY uv.lock* ./
RUN uv sync --no-dev --no-install-project || true

COPY . .
RUN uv sync --no-dev

EXPOSE 8000
CMD ["uv", "run", "uvicorn", "taproot.main:app", "--host", "0.0.0.0", "--port", "8000"]
