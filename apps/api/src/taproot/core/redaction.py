"""Secret scrubbing.

This module currently covers **secret-shaped** strings so that nothing
secret-like can reach a log record (T-05). The full redaction pipeline —
``user_name`` hashing, email masking, and PII patterns (ARCHITECTURE.md §8.3) —
is completed in T-22 and will extend this module. The public entry point used
by the LLM boundary will build on :func:`redact_secrets`.
"""

from __future__ import annotations

import re

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


def redact_secrets(text: str) -> str:
    """Return ``text`` with any secret-shaped substring masked."""
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text
