"""Redaction pipeline (ARCHITECTURE.md §8.3).

Everything crossing into an LLM prompt *or* into a persisted step payload runs
through here first — **no exceptions** (the local model reduces blast radius but
does not remove the obligation). Covers:

| Pattern | Action |
|---|---|
| ``user_name`` field | ``user_<sha256[:8]>`` (stable → still countable) |
| email addresses | mask the local part (``a***@host``) |
| ``Bearer``/``api_key=``/JWT shapes | ``<REDACTED_TOKEN>`` |
| credit-card / national-ID patterns | ``<REDACTED_PII>`` |
| known secret prefixes (``glpat-``, ``sntrys_``, ``AKIA``) | ``<REDACTED_SECRET>`` |

``redact_secrets`` (secret-shaped strings) is used by the logging processor
(T-05); ``redact_text``/``redact_value`` are the full pipeline used at the LLM
boundary and before persisting step payloads (T-22).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any

from taproot.core.models import LLMMessage

# (pattern, replacement) — order matters; broadest last.
_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE), "Bearer <REDACTED_TOKEN>"),
    (re.compile(r"\bglpat-[A-Za-z0-9\-_]{16,}"), "<REDACTED_SECRET>"),
    (re.compile(r"\bsntrys_[A-Za-z0-9\-_]{10,}"), "<REDACTED_SECRET>"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "<REDACTED_SECRET>"),
    # JWT: three base64url segments separated by dots.
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"), "<REDACTED_TOKEN>"),
    # key=value / key: value shaped assignments of sensitive names.
    (
        re.compile(r"(?i)\b(api[_-]?key|token|password|secret)\b\s*[=:]\s*[^\s,;\"']+"),
        r"\1=<REDACTED_SECRET>",
    ),
]

# Email — keep the first local-part char + domain so counts/dedup survive.
_EMAIL_RE = re.compile(r"\b([A-Za-z0-9])[A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")

_PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Credit-card-shaped: 13–16 digits, optionally spaced/dashed in groups.
    (re.compile(r"\b(?:\d[ -]?){13,16}\b"), "<REDACTED_PII>"),
    # National-ID / SSN-shaped.
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "<REDACTED_PII>"),
]

# JSON keys whose value is a raw username → hash instead of pattern-scrub.
_USER_KEYS = {"user_name", "username", "user"}


def _mask_email(match: re.Match[str]) -> str:
    return f"{match.group(1)}***{match.group(2)}"


def redact_secrets(text: str) -> str:
    """Return ``text`` with any secret-shaped substring masked (logging + prompts)."""
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_text(text: str) -> str:
    """Full free-text redaction: secrets, then emails, then PII patterns."""
    text = redact_secrets(text)
    text = _EMAIL_RE.sub(_mask_email, text)
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def hash_user(name: str | None) -> str | None:
    """Stable pseudonym for a username: same input → same hash, so distinct users
    stay countable without the raw value ever leaving the boundary."""
    if not name:
        return None
    return "user_" + hashlib.sha256(name.encode()).hexdigest()[:8]


def redact_value(value: Any) -> Any:
    """Recursively redact a JSON-ish structure. String leaves are scrubbed; any
    ``user_name``-like key has its value hashed rather than pattern-matched."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        redacted: dict[Any, Any] = {}
        for key, val in value.items():
            if key in _USER_KEYS and isinstance(val, str):
                redacted[key] = hash_user(val)
            else:
                redacted[key] = redact_value(val)
        return redacted
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    return value


def redact_messages(messages: list[LLMMessage]) -> list[LLMMessage]:
    """Redact every message before it reaches the model (the LLM boundary)."""
    return [LLMMessage(role=m.role, content=redact_text(m.content)) for m in messages]
