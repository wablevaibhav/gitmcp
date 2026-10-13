"""End-to-end MCP tests: a real MCP Client talks to the server in-process.

GitHub itself is mocked with respx; no network or real token is involved.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from collections.abc import Callable
from typing import Any

import pytest
import respx
from mcp import Client

from gitmcp.config import Settings
from gitmcp.logging_utils import RedactingFilter, redact
from gitmcp.retry import RetryPolicy
from gitmcp.server import create_server

from .conftest import FAKE_TOKEN, repo_json

READ_TOOLS = {
    "github_search_repositories",
    "github_get_repository",
    "github_list_repository_contents",
    "github_get_file_contents",
    "github_search_code",
    "github_list_issues",
    "github_get_issue",
    "github_list_issue_comments",
    "github_list_pull_requests",
    "github_get_pull_request",
    "github_list_pull_request_files",
    "github_list_pull_request_reviews",
    "github_list_commits",
    "github_get_commit",
}
WRITE_TOOLS = {
    "github_create_issue",
    "github_add_issue_comment",
    "github_create_branch",
    "github_create_pull_request",
}


class OneShotClient:
    """Opens a fresh in-process MCP client per call.

    Avoids holding an anyio task group open across a pytest fixture boundary.
    """

    def __init__(self, server: Any) -> None:
        self._server = server

    async def list_tools(self) -> Any:
        async with Client(self._server) as c:
            return await c.list_tools()

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        async with Client(self._server) as c:
            return await c.call_tool(name, arguments)


@pytest.fixture
def mcp_client(settings: Settings, policy: RetryPolicy, mock_api: respx.MockRouter) -> Any:
    return OneShotClient(create_server(settings, policy=policy))


def text_of(result: Any) -> str:
    return "".join(getattr(b, "text", "") for b in result.content)


async def test_all_read_tools_registered_write_tools_hidden(mcp_client: Any) -> None:
    tools = await mcp_client.list_tools()
    names = {t.name for t in tools.tools}
    assert names == READ_TOOLS
    assert all(t.description for t in tools.tools)


async def test_write_tools_registered_only_when_enabled(
    make_settings: Callable[..., Settings], policy: RetryPolicy, mock_api: respx.MockRouter
) -> None:
    server = create_server(make_settings(github_enable_write_tools=True), policy=policy)
    names = {t.name for t in (await OneShotClient(server).list_tools()).tools}
    assert names == READ_TOOLS | WRITE_TOOLS


async def test_input_schema_is_descriptive_and_strict(mcp_client: Any) -> None:
    tools = {t.name: t for t in (await mcp_client.list_tools()).tools}
    schema = tools["github_list_issues"].input_schema
    inner = schema["$defs"]["ListIssuesInput"]
    assert inner["additionalProperties"] is False
    assert inner["properties"]["state"]["enum"] == ["open", "closed", "all"]
    assert inner["properties"]["per_page"]["maximum"] == 100
    assert "Repository owner" in inner["properties"]["owner"]["description"]


async def test_call_tool_success_structured_output(
    mcp_client: Any, mock_api: respx.MockRouter
) -> None:
    mock_api.get("/repos/octocat/Hello-World").respond(200, json=repo_json())
    result = await mcp_client.call_tool(
        "github_get_repository", {"params": {"owner": "octocat", "repo": "Hello-World"}}
    )
    assert not result.is_error
    data = result.structured_content or json.loads(text_of(result))
    assert data["default_branch"] == "main"
    assert data["license"] == "MIT"


async def test_invalid_input_rejected_without_http_call(
    mcp_client: Any, mock_api: respx.MockRouter
) -> None:
    route = mock_api.get(url__regex=r".*")
    result = await mcp_client.call_tool(
        "github_list_issues", {"params": {"owner": "o", "repo": "r", "per_page": 500}}
    )
    assert result.is_error
    assert not route.called


async def test_unknown_field_rejected(mcp_client: Any) -> None:
    result = await mcp_client.call_tool(
        "github_get_repository", {"params": {"owner": "o", "repo": "r", "url": "http://evil"}}
    )
    assert result.is_error


async def test_github_404_becomes_safe_actionable_error(
    mcp_client: Any, mock_api: respx.MockRouter
) -> None:
    mock_api.get("/repos/o/nope").respond(404, json={"message": "Not Found"})
    result = await mcp_client.call_tool(
        "github_get_repository", {"params": {"owner": "o", "repo": "nope"}}
    )
    assert result.is_error
    msg = text_of(result)
    assert "NotFoundError" in msg and "Hint" in msg
    assert "Traceback" not in msg


async def test_token_never_in_error_output(mcp_client: Any, mock_api: respx.MockRouter) -> None:
    # A hostile/odd upstream message echoing the token must not reach the client.
    mock_api.get("/repos/o/r").respond(401, json={"message": f"Bad credentials for {FAKE_TOKEN}"})
    result = await mcp_client.call_tool(
        "github_get_repository", {"params": {"owner": "o", "repo": "r"}}
    )
    assert result.is_error
    assert FAKE_TOKEN not in text_of(result)
    assert "AuthenticationError" in text_of(result)


async def test_unexpected_exception_is_hidden(
    mcp_client: Any, mock_api: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gitmcp.github import repositories

    async def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError(f"secret internals {FAKE_TOKEN}")

    monkeypatch.setattr(repositories.RepositoryService, "get_repository", boom)
    result = await mcp_client.call_tool(
        "github_get_repository", {"params": {"owner": "o", "repo": "r"}}
    )
    assert result.is_error
    assert "secret internals" not in text_of(result)
    assert FAKE_TOKEN not in text_of(result)


async def test_rate_limit_exhaustion_message(mcp_client: Any, mock_api: respx.MockRouter) -> None:
    mock_api.get("/repos/o/r").respond(429, headers={"Retry-After": "3600"}, json={})
    result = await mcp_client.call_tool(
        "github_get_repository", {"params": {"owner": "o", "repo": "r"}}
    )
    assert result.is_error and "RateLimitError" in text_of(result)
    assert "retry budget" in text_of(result)


async def test_end_to_end_issue_listing_with_pagination(
    mcp_client: Any, mock_api: respx.MockRouter
) -> None:
    mock_api.get("/repos/o/r/issues").respond(
        200,
        headers={"link": '<https://api.github.com/x?page=2>; rel="next"'},
        json=[
            {"number": 1, "title": "A", "state": "open", "html_url": "u1"},
            {"number": 2, "title": "PR", "state": "open", "html_url": "u2", "pull_request": {}},
        ],
    )
    result = await mcp_client.call_tool(
        "github_list_issues", {"params": {"owner": "o", "repo": "r", "per_page": 2}}
    )
    data = result.structured_content or json.loads(text_of(result))
    assert [i["number"] for i in data["items"]] == [1]
    assert data["has_next_page"] is True


# ---------------------------------------------------------------- redaction


def test_redact_helper() -> None:
    assert FAKE_TOKEN not in redact(f"token={FAKE_TOKEN}")
    assert "github_pat_" + "x" * 30 not in redact("github_pat_" + "x" * 30)
    assert "abc123secret" not in redact("Authorization: Bearer abc123secret")
    assert "custom-secret-value" not in redact("x custom-secret-value", ("custom-secret-value",))


def test_logging_filter_redacts_args_and_exceptions(caplog: pytest.LogCaptureFixture) -> None:
    flt = RedactingFilter((FAKE_TOKEN,))
    try:
        raise ValueError(f"failed with {FAKE_TOKEN}")
    except ValueError:
        import sys as _sys

        rec = logging.LogRecord(
            "t", logging.ERROR, "f", 1, "oops %s", (FAKE_TOKEN,), _sys.exc_info()
        )
    flt.filter(rec)
    rendered = rec.getMessage() + (rec.exc_text or "")
    assert FAKE_TOKEN not in rendered
    assert "REDACTED" in rendered


def test_stdio_server_starts_and_stdout_is_clean() -> None:
    """Spawn the real module over stdio; stdout must be pure JSON-RPC, logs on stderr."""
    init = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "0"},
            },
        }
    )
    proc = subprocess.run(
        [sys.executable, "-m", "gitmcp"],
        input=init + "\n",
        capture_output=True,
        text=True,
        timeout=30,
        env={
            "PATH": "",
            "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
            "GITHUB_TOKEN": FAKE_TOKEN,
            "GITMCP_LOG_LEVEL": "INFO",
        },
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert lines, f"no protocol output; stderr={proc.stderr[:300]}"
    for ln in lines:
        json.loads(ln)  # raises if any non-protocol text leaked to stdout
    assert json.loads(lines[0])["id"] == 1
    assert FAKE_TOKEN not in proc.stdout + proc.stderr
    assert "gitmcp" in proc.stderr


async def test_lifespan_closes_http_client(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gitmcp.github.client import GitHubClient

    closed: list[bool] = []
    original = GitHubClient.aclose

    async def spy(self: GitHubClient) -> None:
        closed.append(True)
        await original(self)

    monkeypatch.setattr(GitHubClient, "aclose", spy)
    async with Client(create_server(settings)) as c:
        await c.list_tools()
        assert closed == []
    assert closed == [True]
