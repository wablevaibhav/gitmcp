"""Optional live tests. Skipped unless GITMCP_TEST_TOKEN and GITMCP_TEST_REPO are set.

These only perform read operations against the repository you name.
    GITMCP_TEST_TOKEN=<fine-grained token>  GITMCP_TEST_REPO=owner/repo  pytest -m integration
"""

from __future__ import annotations

import os

import pytest

from gitmcp.config import Settings
from gitmcp.github.client import GitHubClient
from gitmcp.github.commits import CommitService
from gitmcp.github.repositories import RepositoryService
from gitmcp.schemas import inputs as i

TOKEN = os.environ.get("GITMCP_TEST_TOKEN")
REPO = os.environ.get("GITMCP_TEST_REPO", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
    pytest.mark.skipif(not (TOKEN and "/" in REPO), reason="integration env vars not set"),
]


async def test_live_repository_and_commits() -> None:
    owner, repo = REPO.split("/", 1)
    settings = Settings(_env_file=None, github_token=TOKEN)
    client = GitHubClient(settings)
    try:
        details = await RepositoryService(client, settings).get_repository(
            i.GetRepositoryInput(owner=owner, repo=repo)
        )
        assert details.full_name.lower() == REPO.lower()
        commits = await CommitService(client, settings).list_commits(
            i.ListCommitsInput(owner=owner, repo=repo, per_page=1)
        )
        assert len(commits.items) <= 1
    finally:
        await client.aclose()
