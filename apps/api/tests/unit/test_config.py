from __future__ import annotations

import pytest

from taproot.core.config import load_settings
from taproot.core.exceptions import ConfigurationError


def test_missing_required_raises_named_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAPROOT_DATABASE_URL", raising=False)
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "TAPROOT_DATABASE_URL" in str(excinfo.value)


def test_valid_settings_load(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAPROOT_DATABASE_URL", "postgresql+asyncpg://u:p@db/taproot")
    monkeypatch.setenv("TAPROOT_ENV", "staging")
    settings = load_settings()
    assert settings.database_url.endswith("taproot")
    assert settings.env == "staging"
    assert settings.is_production is False
    # Defaults preserved.
    assert settings.agent_max_tokens == 120_000
    assert settings.investigation_retention_days == 90


def test_prefix_and_agent_tuning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAPROOT_DATABASE_URL", "postgresql+asyncpg://x")
    monkeypatch.setenv("TAPROOT_AGENT_CONCURRENCY", "9")
    settings = load_settings()
    assert settings.agent_concurrency == 9
