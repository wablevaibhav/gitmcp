"""Shared fixtures. Nothing here touches the network or a real token."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
import respx

from gitmcp.config import Settings
from gitmcp.github.client import GitHubClient
from gitmcp.retry import RetryPolicy

API = "https://api.github.com"
FAKE_TOKEN = "ghp_" + "A" * 36  # shaped like a classic PAT, obviously not real


class Sleeper:
    """Records requested sleeps instead of waiting."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


@pytest.fixture
def sleeper() -> Sleeper:
    return Sleeper()


@pytest.fixture
def policy(sleeper: Sleeper) -> RetryPolicy:
    # Deterministic jitter: always the upper bound, so waits are predictable.
    return RetryPolicy(
        max_retries=3,
        base_delay=0.5,
        max_delay=4.0,
        max_rate_limit_wait=30.0,
        sleep=sleeper,
        uniform=lambda _lo, hi: hi,
    )


@pytest.fixture
def make_settings() -> Callable[..., Settings]:
    def _make(**overrides: Any) -> Settings:
        base: dict[str, Any] = {"github_token": FAKE_TOKEN}
        base.update(overrides)
        return Settings(_env_file=None, **base)

    return _make


@pytest.fixture
def settings(make_settings: Callable[..., Settings]) -> Settings:
    return make_settings()


@pytest.fixture
def mock_api() -> respx.MockRouter:
    with respx.mock(base_url=API, assert_all_called=False) as router:
        yield router


@pytest.fixture
async def client(
    settings: Settings, policy: RetryPolicy, mock_api: respx.MockRouter
) -> AsyncIterator[GitHubClient]:
    c = GitHubClient(settings, policy=policy)
    yield c
    await c.aclose()


def repo_json(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "full_name": "octocat/Hello-World",
        "name": "Hello-World",
        "owner": {"login": "octocat"},
        "description": "My first repository",
        "html_url": "https://github.com/octocat/Hello-World",
        "private": False,
        "visibility": "public",
        "language": "Python",
        "stargazers_count": 42,
        "forks_count": 3,
        "open_issues_count": 1,
        "default_branch": "main",
        "license": {"spdx_id": "MIT", "name": "MIT License"},
        "topics": ["demo"],
        "clone_url": "https://github.com/octocat/Hello-World.git",
        "url": f"{API}/repos/octocat/Hello-World",
    }
    base.update(kw)
    return base
