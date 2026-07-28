"""Application configuration (ARCHITECTURE.md §10).

Every environment variable the platform reads is declared here. A missing
required variable fails at startup with a named error, never at first use.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from taproot.core.exceptions import ConfigurationError

Environment = Literal["development", "staging", "production"]


class Settings(BaseSettings):
    """Typed settings, read from ``TAPROOT_``-prefixed environment variables.

    Keep this in lockstep with ``.env.example`` and ARCHITECTURE.md §10.
    """

    model_config = SettingsConfigDict(
        env_prefix="TAPROOT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Core ---------------------------------------------------------------
    env: Environment = "development"
    database_url: str  # required — no sensible default
    redis_url: str = "redis://localhost:6379/0"
    log_level: str = "INFO"

    # --- Auth ---------------------------------------------------------------
    keycloak_url: str | None = None
    keycloak_realm: str | None = None
    keycloak_audience: str = "taproot-api"

    # --- Secrets ------------------------------------------------------------
    secret_store: Literal["vault", "local"] = "local"  # noqa: S105  (store kind, not a secret)
    vault_addr: str | None = None
    vault_token: str | None = None
    # Dev-only key for LocalEncryptedSecretStore; must be set when secret_store=local.
    local_secret_key: str | None = None

    # --- LLM ----------------------------------------------------------------
    llm_base_url: str = "http://vllm:8001/v1"
    llm_model: str = "Qwen2.5-Coder-32B-Instruct"
    llm_timeout_s: int = 120

    # --- GitLab (platform-level, group discovery) ---------------------------
    gitlab_url: str | None = None
    gitlab_token: str | None = None  # read_api scope ONLY

    # --- Agent tuning -------------------------------------------------------
    agent_max_tokens: int = 120_000
    agent_node_timeout_s: int = 45
    agent_max_duration_s: int = 300
    agent_max_threads: int = 5
    agent_concurrency: int = 5

    # --- Retention ----------------------------------------------------------
    investigation_retention_days: int = 90

    # --- Observability ------------------------------------------------------
    service_name: str = "taproot-api"
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str | None = None

    @property
    def is_production(self) -> bool:
        return self.env == "production"


def load_settings() -> Settings:
    """Build :class:`Settings`, converting validation failures into a
    :class:`ConfigurationError` that names the offending variables."""
    try:
        return Settings()
    except ValidationError as exc:
        missing = [
            "TAPROOT_" + ".".join(str(p) for p in err["loc"]).upper()
            for err in exc.errors()
            if err["type"] == "missing"
        ]
        if missing:
            raise ConfigurationError(
                f"Missing required configuration: {', '.join(missing)}"
            ) from exc
        raise ConfigurationError(f"Invalid configuration: {exc}") from exc


@lru_cache
def get_settings() -> Settings:
    """Cached accessor used by the application at runtime."""
    return load_settings()
