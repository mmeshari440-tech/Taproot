"""GitLab-backed ``CodeResolver`` adapter for the agent's ``code_locate`` node.

Bridges the agent's port (``agent.context.CodeResolver``) to the GitLab client +
the project's registered repos, keeping ``agent`` free of ``db``/``integrations``.
A missing file (or any GitLab error) resolves to ``None`` so the node degrades
gracefully instead of failing the run (ARCHITECTURE.md §6.5).
"""

from __future__ import annotations

from collections.abc import Sequence

from taproot.core.exceptions import IntegrationError, NotFoundError
from taproot.core.logging import get_logger
from taproot.core.models import RepoRef
from taproot.integrations.gitlab import GitLabClient

_log = get_logger(__name__)


class GitLabCodeResolver:
    """Preloaded repo refs + read-only GitLab file fetch."""

    def __init__(self, gitlab_client: GitLabClient, repo_refs: Sequence[RepoRef]) -> None:
        self._gitlab = gitlab_client
        self._repos = list(repo_refs)

    def repos(self) -> Sequence[RepoRef]:
        return self._repos

    async def fetch_file(self, gitlab_project_id: int, path: str, ref: str) -> str | None:
        try:
            file = await self._gitlab.get_file(gitlab_project_id, path, ref)
        except NotFoundError:
            return None
        except IntegrationError as exc:
            _log.info("code_locate_fetch_failed", path=path, ref=ref, error=str(exc))
            return None
        return file.content
