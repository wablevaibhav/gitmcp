"""MCP server: tool registration, lifecycle and stdio entry point.

Business logic lives in ``gitmcp.github.*``; this module only adapts it to MCP.
"""

from __future__ import annotations

import functools
import logging
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from gitmcp import __version__
from gitmcp.config import Settings
from gitmcp.errors import GitMCPError
from gitmcp.github.client import GitHubClient
from gitmcp.github.commits import CommitService
from gitmcp.github.issues import IssueService
from gitmcp.github.pull_requests import PullRequestService
from gitmcp.github.repositories import RepositoryService
from gitmcp.logging_utils import configure_logging, redact
from gitmcp.retry import RetryPolicy
from gitmcp.schemas import inputs as i
from gitmcp.schemas import outputs as o

log = logging.getLogger("gitmcp.server")

InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)

INSTRUCTIONS = (
    "GitHub tools for exploring repositories, source files, issues, pull requests and commits. "
    "Every tool takes a single 'params' object. Lists are paginated: pass 'page' and 'per_page' "
    "and check 'has_next_page'. Large text is truncated and flagged. Write tools exist only when "
    "the operator enabled them."
)

_READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True
)
_WRITE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True
)


@dataclass
class Services:
    client: GitHubClient
    repos: RepositoryService
    issues: IssueService
    pulls: PullRequestService
    commits: CommitService

    @classmethod
    def build(
        cls,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
        policy: RetryPolicy | None = None,
    ) -> Services:
        client = GitHubClient(settings, transport=transport, policy=policy)
        return cls(
            client=client,
            repos=RepositoryService(client, settings),
            issues=IssueService(client, settings),
            pulls=PullRequestService(client, settings),
            commits=CommitService(client, settings),
        )


def _guarded(
    fn: Callable[[InputT], Awaitable[OutputT]], secrets: tuple[str, ...]
) -> Callable[[InputT], Awaitable[OutputT]]:
    """Turn expected failures into safe ToolErrors; hide everything unexpected."""

    @functools.wraps(fn)
    async def wrapper(params: InputT) -> OutputT:
        try:
            return await fn(params)
        except GitMCPError as exc:
            log.info("tool %s failed: %s", fn.__name__, exc.message)
            raise ToolError(redact(exc.safe_message(), secrets)) from None
        except Exception as exc:
            # Log only the type: messages from libraries may echo request details.
            log.error("tool %s crashed with %s", fn.__name__, type(exc).__name__)
            raise ToolError("Internal error while calling GitHub. See server logs.") from None

    return wrapper


def register_tools(server: MCPServer[Any], services: Services, settings: Settings) -> list[str]:
    """Register all tools; returns their names. Write tools only when enabled."""
    secrets = (settings.token_value,) if settings.token_value else ()
    names: list[str] = []

    def add(
        name: str,
        description: str,
        fn: Callable[[Any], Awaitable[Any]],
        annotations: ToolAnnotations = _READ_ONLY,
    ) -> None:
        # MCP derives the tool's input schema from the function signature, so name the
        # wrapper after the tool and keep its single annotated 'params' argument.
        wrapped = _guarded(fn, secrets)
        server.add_tool(wrapped, name=name, description=description, annotations=annotations)
        names.append(name)

    r, iss, pr, cm = services.repos, services.issues, services.pulls, services.commits

    async def github_search_repositories(
        params: i.SearchRepositoriesInput,
    ) -> o.RepositorySearchResult:
        return await r.search_repositories(params)

    async def github_get_repository(params: i.GetRepositoryInput) -> o.RepositoryDetails:
        return await r.get_repository(params)

    async def github_list_repository_contents(params: i.ListContentsInput) -> o.DirectoryListing:
        return await r.list_contents(params)

    async def github_get_file_contents(params: i.GetFileInput) -> o.FileContents:
        return await r.get_file_contents(params)

    async def github_search_code(params: i.SearchCodeInput) -> o.CodeSearchResult:
        return await r.search_code(params)

    async def github_list_issues(params: i.ListIssuesInput) -> o.IssueList:
        return await iss.list_issues(params)

    async def github_get_issue(params: i.GetIssueInput) -> o.IssueDetails:
        return await iss.get_issue(params)

    async def github_list_issue_comments(params: i.ListIssueCommentsInput) -> o.IssueCommentList:
        return await iss.list_comments(params)

    async def github_list_pull_requests(params: i.ListPullRequestsInput) -> o.PullRequestList:
        return await pr.list_pull_requests(params)

    async def github_get_pull_request(params: i.GetPullRequestInput) -> o.PullRequestDetails:
        return await pr.get_pull_request(params)

    async def github_list_pull_request_files(
        params: i.ListPullRequestFilesInput,
    ) -> o.ChangedFileList:
        return await pr.list_files(params)

    async def github_list_pull_request_reviews(
        params: i.ListPullRequestReviewsInput,
    ) -> o.ReviewList:
        return await pr.list_reviews(params)

    async def github_list_commits(params: i.ListCommitsInput) -> o.CommitList:
        return await cm.list_commits(params)

    async def github_get_commit(params: i.GetCommitInput) -> o.CommitDetails:
        return await cm.get_commit(params)

    add(
        "github_search_repositories",
        "Search GitHub repositories. Supports language, topic and minimum-stars filters.",
        github_search_repositories,
    )
    add(
        "github_get_repository",
        "Get metadata for one repository: default branch, description, visibility, license, URLs.",
        github_get_repository,
    )
    add(
        "github_list_repository_contents",
        "List files and directories at a path (default: root) on a branch, tag or commit.",
        github_list_repository_contents,
    )
    add(
        "github_get_file_contents",
        "Read one text file. Fails clearly for directories, binary files and files over the "
        "size limit.",
        github_get_file_contents,
    )
    add(
        "github_search_code",
        "Search code across GitHub. Requires GITHUB_TOKEN and a scoping qualifier "
        "such as repo:owner/name.",
        github_search_code,
    )
    add(
        "github_list_issues",
        "List repository issues with state, label and assignee filters. Pull requests are "
        "excluded unless include_pull_requests is true.",
        github_list_issues,
    )
    add("github_get_issue", "Get one issue with labels, assignees and body.", github_get_issue)
    add(
        "github_list_issue_comments",
        "List the comments on an issue or pull request (bodies are truncated when very long).",
        github_list_issue_comments,
    )
    add(
        "github_list_pull_requests",
        "List pull requests filtered by state, base branch and head ('owner:branch').",
        github_list_pull_requests,
    )
    add(
        "github_get_pull_request",
        "Get one pull request: branches, author, merge status, size and requested reviewers.",
        github_get_pull_request,
    )
    add(
        "github_list_pull_request_files",
        "List files changed by a pull request with additions, deletions and (truncated) patches.",
        github_list_pull_request_files,
    )
    add(
        "github_list_pull_request_reviews",
        "List reviews of a pull request with their approval / change-request state.",
        github_list_pull_request_reviews,
    )
    add(
        "github_list_commits",
        "List commits, optionally for a branch/tag/SHA, a path, or an author.",
        github_list_commits,
    )
    add(
        "github_get_commit",
        "Get one commit: message, author, parents, stats and changed files with truncated patches.",
        github_get_commit,
    )

    if settings.github_enable_write_tools:

        async def github_create_issue(params: i.CreateIssueInput) -> o.CreatedIssue:
            return await iss.create_issue(params)

        async def github_add_issue_comment(params: i.AddIssueCommentInput) -> o.CreatedComment:
            return await iss.add_comment(params)

        async def github_create_branch(params: i.CreateBranchInput) -> o.CreatedBranch:
            return await r.create_branch(params)

        async def github_create_pull_request(
            params: i.CreatePullRequestInput,
        ) -> o.CreatedPullRequest:
            return await pr.create_pull_request(params)

        add("github_create_issue", "WRITE: create an issue.", github_create_issue, _WRITE)
        add(
            "github_add_issue_comment",
            "WRITE: add a comment to an issue or pull request.",
            github_add_issue_comment,
            _WRITE,
        )
        add(
            "github_create_branch",
            "WRITE: create a branch from a ref (default: the default branch).",
            github_create_branch,
            _WRITE,
        )
        add(
            "github_create_pull_request",
            "WRITE: open a pull request from an existing head branch.",
            github_create_pull_request,
            _WRITE,
        )
    return names


def create_server(
    settings: Settings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    policy: RetryPolicy | None = None,
) -> MCPServer[Any]:
    """Build the MCP server. ``transport``/``policy`` let tests inject mocks."""
    settings = settings or Settings()
    services = Services.build(settings, transport=transport, policy=policy)

    @asynccontextmanager
    async def lifespan(_: MCPServer[Any]) -> AsyncIterator[None]:
        try:
            yield None
        finally:
            await services.client.aclose()
            log.info("GitHub HTTP client closed")

    server: MCPServer[Any] = MCPServer(
        name="gitmcp",
        title="GitMCP",
        instructions=INSTRUCTIONS,
        version=__version__,
        lifespan=lifespan,
        log_level=settings.gitmcp_log_level,
    )
    names = register_tools(server, services, settings)
    log.info(
        "gitmcp %s ready: %d tools, writes %s, auth %s",
        __version__,
        len(names),
        "ENABLED" if settings.github_enable_write_tools else "disabled",
        "token" if settings.token_value else "anonymous",
    )
    return server


def main() -> None:
    """Console entry point: run over stdio."""
    try:
        settings = Settings()
    except Exception as exc:  # pydantic ValidationError: print field names only
        names = ", ".join(str(e.get("loc", ["?"])[0]) for e in getattr(exc, "errors", lambda: [])())
        print(f"gitmcp: invalid configuration ({names or type(exc).__name__}).", file=sys.stderr)
        raise SystemExit(2) from None
    secrets = (settings.token_value,) if settings.token_value else ()
    configure_logging(settings.gitmcp_log_level, secrets)
    create_server(settings).run("stdio")


if __name__ == "__main__":
    main()
