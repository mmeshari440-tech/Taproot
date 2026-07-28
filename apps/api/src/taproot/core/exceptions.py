"""Typed exceptions used across the platform.

Errors surfaced to users must be actionable and name the failing system.
``"Something went wrong"`` is a bug (ARCHITECTURE.md §8.4).
"""

from __future__ import annotations


class TaprootError(Exception):
    """Base class for all Taproot errors."""


class ConfigurationError(TaprootError):
    """Raised at startup when configuration is missing or invalid.

    Must fail fast with a named variable — never a bare ``KeyError`` at first use.
    """


class SecretStoreError(TaprootError):
    """Raised when a secret cannot be stored, retrieved, or when a store refuses
    to start in an environment it is not permitted to run in."""
