"""Versioned Jinja2 prompt templates (ARCHITECTURE.md §6.7).

Prompts are code: reviewed, diffed, never built by string concatenation in a
node body. Every ``*.jinja2`` file here is one versioned prompt fragment
(``_v1`` in the filename); a prompt change ships as a new ``_v2`` file, not an
edit-in-place, so an old run's exact prompt stays reconstructible.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

_ENV = Environment(
    loader=FileSystemLoader(Path(__file__).parent),
    autoescape=False,  # noqa: S701 - plain-text LLM prompts, not HTML; escaping would corrupt content
    trim_blocks=True,
    lstrip_blocks=True,
    undefined=StrictUndefined,
)


def render(template: str, **context: Any) -> str:
    """Render one of the ``*.jinja2`` templates in this package."""
    return _ENV.get_template(template).render(**context)
