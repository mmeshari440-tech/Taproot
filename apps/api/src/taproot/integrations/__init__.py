"""External-system clients: elastic, sentry, appdynamics, gitlab, keycloak, llm.

Each exposes a class per the integration client contract (ARCHITECTURE.md §3):
async construction, ``test_connection()``, read-only methods returning normalized
domain models, 30s timeout, retries on 5xx/429 only. Depends only on ``core``.
Implemented from Sprint 1 (T-09) / Sprint 2 (T-13–T-16) onward.
"""
