from __future__ import annotations

import json

import pytest

from taproot.core.logging import _redact_processor, configure_logging
from taproot.core.redaction import redact_secrets


@pytest.mark.parametrize(
    ("raw", "leaked"),
    [
        ("Authorization: Bearer abc.def.ghi", "abc.def.ghi"),
        ("token glpat-ABCDEFGHIJKLMNOPQRST", "glpat-ABCDEFGHIJKLMNOPQRST"),
        ("sentry sntrys_0123456789abcdef", "sntrys_0123456789abcdef"),
        ("aws AKIAIOSFODNN7EXAMPLE key", "AKIAIOSFODNN7EXAMPLE"),
        ("jwt eyJhbGc.eyJzdWIi.SflKxwRJ", "eyJhbGc.eyJzdWIi.SflKxwRJ"),
        ("api_key=supersecretvalue123", "supersecretvalue123"),
        ("password: hunter2hunter2", "hunter2hunter2"),
    ],
)
def test_redact_secrets_masks_known_shapes(raw: str, leaked: str) -> None:
    out = redact_secrets(raw)
    assert leaked not in out
    assert "REDACTED" in out


def test_redact_secrets_leaves_ordinary_text() -> None:
    assert redact_secrets("investigation completed in 820ms") == (
        "investigation completed in 820ms"
    )


def test_log_processor_scrubs_every_string_value() -> None:
    event = {
        "event": "integration_test",
        "authorization": "Bearer topsecrettoken123",
        "detail": "connecting with api_key=leakme12345",
        "latency_ms": 42,
    }
    scrubbed = _redact_processor(None, "info", dict(event))
    serialized = json.dumps(scrubbed)
    assert "topsecrettoken123" not in serialized
    assert "leakme12345" not in serialized
    # Non-string values pass through untouched.
    assert scrubbed["latency_ms"] == 42


def test_configure_logging_is_idempotent() -> None:
    configure_logging("DEBUG")
    configure_logging("INFO")  # must not raise
