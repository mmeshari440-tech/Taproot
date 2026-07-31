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


class AuthenticationError(TaprootError):
    """Token missing, malformed, expired, or failing signature/claim checks.

    Maps to HTTP 401 — the caller must (re-)authenticate (ARCHITECTURE.md §8.1).
    """


class AuthorizationError(TaprootError):
    """Valid token, but the caller lacks the required role.

    Maps to HTTP 403 — never conflate with 401 (ARCHITECTURE.md §8.1).
    """


class IntegrationError(TaprootError):
    """An external integration call failed.

    Carries the provider's actual message so the API can surface it verbatim
    (PLAN.md §5.1) rather than a generic ``"Something went wrong"``.
    """


class NotFoundError(TaprootError):
    """A requested entity does not exist. Maps to HTTP 404."""


class PreconditionError(TaprootError):
    """A required precondition is not met (e.g. Elastic not verified before an
    investigation). Maps to HTTP 422 with an actionable message."""
