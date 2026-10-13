"""Business-logic tests: services against mocked GitHub responses (no MCP involved)."""

from __future__ import annotations

import base64
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx

from gitmcp.config import Settings
from gitmcp.errors import (
    AuthenticationError,
    ContentError,
    GitHubAPIError,
    NotFoundError,
    WriteDisabledError,
)
from gitmcp.github.client import GitHubClient
from gitmcp.github.commits import CommitService
from gitmcp.github.issues import IssueService
from gitmcp.github.pull_requests import PullRequestService
from gitmcp.github.repositories import RepositoryService
from gitmcp.schemas import inputs as i

from .conftest import repo_json


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def file_json(content: bytes, **kw: Any) -> dict[str, Any]:
    d = {
        "type": "file",
        "name": "a.txt",
        "path": "a.txt",
        "sha": "abc",
        "size": len(content),
        "encoding": "base64",
        "content": b64(content),
        "html_url": "https://github.com/o/r/blob/main/a.txt",
    }
    d.update(kw)
    return d


# ---------------------------------------------------------------- repositories


async def test_search_repositories_builds_query_and_paginates(
    client: GitHubClient, settings: Settings, mock_api: respx.MockRouter
) -> None:
    route = mock_api.get("/search/repositories").respond(
        200,
        headers={"link": '<https://api.github.com/search/repositories?page=3>; rel="next"'},
        json={"total_count": 100, "incomplete_results": False, "items": [repo_json()]},
    )
    out = await RepositoryService(client, settings).search_repositories(
        i.SearchRepositoriesInput(
            query="web", language="python", topic="api", min_stars=10, page=2, per_page=1
        )
    )
    q = route.calls[0].request.url.params
    assert q["q"] == "web language:python topic:api stars:>=10"
    assert q["page"] == "2" and q["per_page"] == "1"
    assert out.has_next_page is True
    assert out.total_count == 100
    r = out.items[0]
    assert (r.owner, r.name, r.visibility, r.language, r.stars, r.default_branch) == (
        "octocat",
        "Hello-World",
        "public",
        "Python",
        42,
        "main",
    )


async def test_get_repository(
    client: GitHubClient, settings: Settings, mock_api: respx.MockRouter
) -> None:
    mock_api.get("/repos/octocat/Hello-World").respond(200, json=repo_json())
    out = await RepositoryService(client, settings).get_repository(
        i.GetRepositoryInput(owner="octocat", repo="Hello-World")
    )
    assert out.license == "MIT"
    assert out.default_branch == "main"
    assert out.topics == ["demo"]


async def test_get_repository_not_found_has_hint(
    client: GitHubClient, settings: Settings, mock_api: respx.MockRouter
) -> None:
    mock_api.get("/repos/o/missing").respond(404, json={"message": "Not Found"})
    with pytest.raises(NotFoundError) as ei:
        await RepositoryService(client, settings).get_repository(
            i.GetRepositoryInput(owner="o", repo="missing")
        )
    assert ei.value.hint


async def test_list_contents_directory_and_ref(
    client: GitHubClient, settings: Settings, mock_api: respx.MockRouter
) -> None:
    route = mock_api.get("/repos/o/r/contents/src").respond(
        200,
        json=[
            {
                "name": "x.py",
                "path": "src/x.py",
                "type": "file",
                "size": 5,
                "sha": "s",
                "html_url": "h",
                "url": "u",
                "download_url": "d",
            },
            {"name": "pkg", "path": "src/pkg", "type": "dir", "size": 0, "sha": "t"},
        ],
    )
    out = await RepositoryService(client, settings).list_contents(
        i.ListContentsInput(owner="o", repo="r", path="src", ref="dev")
    )
    assert route.calls[0].request.url.params["ref"] == "dev"
    assert [(e.name, e.type) for e in out.entries] == [("x.py", "file"), ("pkg", "dir")]


async def test_list_contents_root(
    client: GitHubClient, settings: Settings, mock_api: respx.MockRouter
) -> None:
    route = mock_api.get("/repos/o/r/contents").respond(200, json=[])
    out = await RepositoryService(client, settings).list_contents(
        i.ListContentsInput(owner="o", repo="r")
    )
    assert route.called and out.entries == []


async def test_get_file_text(
    client: GitHubClient, settings: Settings, mock_api: respx.MockRouter
) -> None:
    mock_api.get("/repos/o/r/contents/a.txt").respond(200, json=file_json("héllo\n".encode()))
    out = await RepositoryService(client, settings).get_file_contents(
        i.GetFileInput(owner="o", repo="r", path="a.txt", ref="v1")
    )
    assert out.content == "héllo\n"


async def test_get_file_binary_rejected(
    client: GitHubClient, settings: Settings, mock_api: respx.MockRouter
) -> None:
    mock_api.get("/repos/o/r/contents/a.png").respond(
        200, json=file_json(b"\x89PNG\r\n\x1a\n\x00\x00\x00")
    )
    with pytest.raises(ContentError, match="binary"):
        await RepositoryService(client, settings).get_file_contents(
            i.GetFileInput(owner="o", repo="r", path="a.png")
        )


async def test_get_file_invalid_utf8_rejected(
    client: GitHubClient, settings: Settings, mock_api: respx.MockRouter
) -> None:
    mock_api.get("/repos/o/r/contents/a.bin").respond(200, json=file_json(b"\xff\xfe\xfa abc"))
    with pytest.raises(ContentError):
        await RepositoryService(client, settings).get_file_contents(
            i.GetFileInput(owner="o", repo="r", path="a.bin")
        )


async def test_get_file_over_limit_rejected_before_decoding(
    client: GitHubClient, make_settings: Callable[..., Settings], mock_api: respx.MockRouter
) -> None:
    s = make_settings(gitmcp_max_file_bytes=100)
    mock_api.get("/repos/o/r/contents/big.txt").respond(
        200, json={"type": "file", "size": 5_000_000, "sha": "x", "encoding": "none", "content": ""}
    )
    with pytest.raises(ContentError, match="over the 100-byte limit"):
        await RepositoryService(client, s).get_file_contents(
            i.GetFileInput(owner="o", repo="r", path="big.txt")
        )


async def test_get_file_missing_inline_content_falls_back_to_raw(
    client: GitHubClient, settings: Settings, mock_api: respx.MockRouter
) -> None:
    route = mock_api.get("/repos/o/r/contents/m.txt").mock(
        side_effect=[
            httpx.Response(
                200, json={"type": "file", "size": 5, "sha": "x", "encoding": "none", "content": ""}
            ),
            httpx.Response(200, content=b"hello"),
        ]
    )
    out = await RepositoryService(client, settings).get_file_contents(
        i.GetFileInput(owner="o", repo="r", path="m.txt")
    )
    assert out.content == "hello"
    assert "raw" in route.calls[1].request.headers["accept"]


async def test_get_file_on_directory(
    client: GitHubClient, settings: Settings, mock_api: respx.MockRouter
) -> None:
    mock_api.get("/repos/o/r/contents/src").respond(200, json=[{"name": "x"}])
    with pytest.raises(ContentError, match="directory"):
        await RepositoryService(client, settings).get_file_contents(
            i.GetFileInput(owner="o", repo="r", path="src")
        )


async def test_get_file_missing(
    client: GitHubClient, settings: Settings, mock_api: respx.MockRouter
) -> None:
    mock_api.get("/repos/o/r/contents/nope").respond(404, json={"message": "Not Found"})
    with pytest.raises(NotFoundError):
        await RepositoryService(client, settings).get_file_contents(
            i.GetFileInput(owner="o", repo="r", path="nope")
        )


async def test_search_code_snippets(
    client: GitHubClient, settings: Settings, mock_api: respx.MockRouter
) -> None:
    route = mock_api.get("/search/code").respond(
        200,
        json={
            "total_count": 1,
            "items": [
                {
                    "name": "a.py",
                    "path": "src/a.py",
                    "sha": "s",
                    "html_url": "h",
                    "repository": {"full_name": "o/r"},
                    "text_matches": [{"fragment": "def parse_config():"}],
                }
            ],
        },
    )
    out = await RepositoryService(client, settings).search_code(
        i.SearchCodeInput(query="parse_config repo:o/r")
    )
    assert "text-match" in route.calls[0].request.headers["accept"]
    assert out.items[0].repository == "o/r"
    assert out.items[0].snippets == ["def parse_config():"]


async def test_search_code_requires_token(
    make_settings: Callable[..., Settings], policy: Any, mock_api: respx.MockRouter
) -> None:
    s = make_settings(github_token=None)
    c = GitHubClient(s, policy=policy)
    route = mock_api.get("/search/code")
    with pytest.raises(AuthenticationError, match="requires authentication"):
        await RepositoryService(c, s).search_code(i.SearchCodeInput(query="x repo:o/r"))
    assert not route.called
    await c.aclose()


async def test_search_code_422_missing_qualifier(
    client: GitHubClient, settings: Settings, mock_api: respx.MockRouter
) -> None:
    mock_api.get("/search/code").respond(
        422, json={"message": "Validation Failed: query must include a qualifier"}
    )
    with pytest.raises(Exception, match="qualifier"):
        await RepositoryService(client, settings).search_code(i.SearchCodeInput(query="foo"))


async def test_malformed_search_response(
    client: GitHubClient, settings: Settings, mock_api: respx.MockRouter
) -> None:
    mock_api.get("/search/repositories").respond(200, json=["not", "an", "object"])
    with pytest.raises(GitHubAPIError, match="Malformed"):
        await RepositoryService(client, settings).search_repositories(
            i.SearchRepositoriesInput(query="x")
        )


async def test_malformed_item_missing_required_field(
    client: GitHubClient, settings: Settings, mock_api: respx.MockRouter
) -> None:
    mock_api.get("/repos/o/r/commits").respond(200, json=[{"commit": {}}])
    with pytest.raises(GitHubAPIError, match="missing 'sha'"):
        await CommitService(client, settings).list_commits(i.ListCommitsInput(owner="o", repo="r"))


# ---------------------------------------------------------------------- issues


def issue_json(n: int, pr: bool = False, **kw: Any) -> dict[str, Any]:
    d: dict[str, Any] = {
        "number": n,
        "title": f"Issue {n}",
        "state": "open",
        "user": {"login": "alice"},
        "labels": [{"name": "bug"}],
        "assignees": [{"login": "bob"}],
        "comments": 2,
        "html_url": f"https://github.com/o/r/issues/{n}",
        "body": "details",
    }
    if pr:
        d["pull_request"] = {"url": "x"}
    d.update(kw)
    return d


async def test_list_issues_filters_prs_and_passes_filters(
    client: GitHubClient, settings: Settings, mock_api: respx.MockRouter
) -> None:
    route = mock_api.get("/repos/o/r/issues").respond(
        200, json=[issue_json(1), issue_json(2, pr=True)]
    )
    svc = IssueService(client, settings)
    out = await svc.list_issues(
        i.ListIssuesInput(owner="o", repo="r", labels=["bug", "help wanted"], assignee="bob")
    )
    q = route.calls[0].request.url.params
    assert q["labels"] == "bug,help wanted" and q["assignee"] == "bob"
    assert [it.number for it in out.items] == [1]

    out = await svc.list_issues(i.ListIssuesInput(owner="o", repo="r", include_pull_requests=True))
    assert [(it.number, it.is_pull_request) for it in out.items] == [(1, False), (2, True)]


async def test_get_issue(
    client: GitHubClient, settings: Settings, mock_api: respx.MockRouter
) -> None:
    mock_api.get("/repos/o/r/issues/7").respond(
        200, json=issue_json(7, milestone={"title": "v1"}, created_at="2024-01-01T00:00:00Z")
    )
    out = await IssueService(client, settings).get_issue(
        i.GetIssueInput(owner="o", repo="r", issue_number=7)
    )
    assert (out.labels, out.assignees, out.author, out.milestone) == (
        ["bug"],
        ["bob"],
        "alice",
        "v1",
    )
    assert out.html_url.endswith("/7")


async def test_comments_truncated_and_paginated(
    client: GitHubClient, make_settings: Callable[..., Settings], mock_api: respx.MockRouter
) -> None:
    s = make_settings(gitmcp_max_comment_chars=100)
    mock_api.get("/repos/o/r/issues/1/comments").respond(
        200,
        headers={"link": '<x>; rel="next"'},
        json=[
            {"id": 1, "user": {"login": "a"}, "body": "x" * 500, "html_url": "h"},
            {"id": 2, "user": None, "body": None, "html_url": "h"},
        ],
    )
    out = await IssueService(client, s).list_comments(
        i.ListIssueCommentsInput(owner="o", repo="r", issue_number=1, per_page=2)
    )
    assert out.has_next_page
    assert out.items[0].body_truncated and len(out.items[0].body) < 200
    assert out.items[1].body == "" and out.items[1].author is None


# ---------------------------------------------------------------- pull requests


def pr_json(**kw: Any) -> dict[str, Any]:
    d: dict[str, Any] = {
        "number": 5,
        "title": "Add feature",
        "state": "open",
        "draft": False,
        "user": {"login": "alice"},
        "head": {"label": "alice:feat", "ref": "feat", "sha": "h1"},
        "base": {"label": "o:main", "ref": "main", "sha": "b1"},
        "html_url": "https://github.com/o/r/pull/5",
        "body": "desc",
        "mergeable": True,
        "mergeable_state": "clean",
        "merged": False,
        "commits": 2,
        "additions": 10,
        "deletions": 1,
        "changed_files": 3,
        "requested_reviewers": [{"login": "bob"}],
        "labels": [{"name": "enhancement"}],
    }
    d.update(kw)
    return d


async def test_list_pull_requests_filters(
    client: GitHubClient, settings: Settings, mock_api: respx.MockRouter
) -> None:
    route = mock_api.get("/repos/o/r/pulls").respond(200, json=[pr_json()])
    out = await PullRequestService(client, settings).list_pull_requests(
        i.ListPullRequestsInput(owner="o", repo="r", state="all", base="main", head="alice:feat")
    )
    q = route.calls[0].request.url.params
    assert (q["state"], q["base"], q["head"]) == ("all", "main", "alice:feat")
    assert out.items[0].head == "alice:feat"


async def test_get_pull_request(
    client: GitHubClient, settings: Settings, mock_api: respx.MockRouter
) -> None:
    mock_api.get("/repos/o/r/pulls/5").respond(200, json=pr_json())
    out = await PullRequestService(client, settings).get_pull_request(
        i.GetPullRequestInput(owner="o", repo="r", pull_number=5)
    )
    assert (out.mergeable, out.mergeable_state, out.changed_files) == (True, "clean", 3)
    assert out.requested_reviewers == ["bob"]
    assert out.head_sha == "h1" and out.base == "o:main"


async def test_pr_files_patch_truncated(
    client: GitHubClient, make_settings: Callable[..., Settings], mock_api: respx.MockRouter
) -> None:
    s = make_settings(gitmcp_max_patch_chars=200)
    mock_api.get("/repos/o/r/pulls/5/files").respond(
        200,
        json=[
            {
                "filename": "a.py",
                "status": "modified",
                "additions": 1,
                "deletions": 0,
                "changes": 1,
                "patch": "+" + "y" * 1000,
            },
            {
                "filename": "img.png",
                "status": "added",
                "additions": 0,
                "deletions": 0,
                "changes": 0,
            },
        ],
    )
    out = await PullRequestService(client, s).list_files(
        i.ListPullRequestFilesInput(owner="o", repo="r", pull_number=5)
    )
    assert out.items[0].patch_truncated and len(out.items[0].patch or "") < 400
    assert out.items[1].patch is None and not out.items[1].patch_truncated


async def test_pr_reviews(
    client: GitHubClient, settings: Settings, mock_api: respx.MockRouter
) -> None:
    mock_api.get("/repos/o/r/pulls/5/reviews").respond(
        200,
        json=[
            {
                "id": 1,
                "user": {"login": "bob"},
                "state": "APPROVED",
                "body": "lgtm",
                "submitted_at": "t",
                "commit_id": "c",
            },
            {"id": 2, "user": {"login": "eve"}, "state": "CHANGES_REQUESTED", "body": ""},
        ],
    )
    out = await PullRequestService(client, settings).list_reviews(
        i.ListPullRequestReviewsInput(owner="o", repo="r", pull_number=5)
    )
    assert [r.state for r in out.items] == ["APPROVED", "CHANGES_REQUESTED"]


# ---------------------------------------------------------------------- commits


async def test_list_commits_params(
    client: GitHubClient, settings: Settings, mock_api: respx.MockRouter
) -> None:
    route = mock_api.get("/repos/o/r/commits").respond(
        200,
        json=[
            {
                "sha": "abc",
                "html_url": "h",
                "author": {"login": "alice"},
                "commit": {"message": "fix", "author": {"name": "Alice", "date": "d"}},
            }
        ],
    )
    out = await CommitService(client, settings).list_commits(
        i.ListCommitsInput(owner="o", repo="r", ref="dev", path="src/a.py", author="alice")
    )
    q = route.calls[0].request.url.params
    assert (q["sha"], q["path"], q["author"]) == ("dev", "src/a.py", "alice")
    assert out.items[0].author_login == "alice" and out.items[0].message == "fix"


async def test_get_commit_details_and_file_cap(
    client: GitHubClient, settings: Settings, mock_api: respx.MockRouter
) -> None:
    files = [{"filename": f"f{n}", "status": "added", "patch": "+x"} for n in range(120)]
    mock_api.get("/repos/o/r/commits/abc123").respond(
        200,
        json={
            "sha": "abc123",
            "html_url": "h",
            "commit": {
                "message": "msg",
                "author": {"name": "A", "date": "d"},
                "committer": {"name": "C"},
            },
            "parents": [{"sha": "p1"}],
            "stats": {"additions": 5, "deletions": 2, "total": 7},
            "files": files,
        },
    )
    out = await CommitService(client, settings).get_commit(
        i.GetCommitInput(owner="o", repo="r", ref="abc123")
    )
    assert out.parents == ["p1"] and out.stats is not None and out.stats.total == 7
    assert len(out.files) == 100 and out.files_truncated
    assert out.committer_name == "C"


# ----------------------------------------------------------------------- writes


async def test_write_services_refuse_when_disabled(
    client: GitHubClient, settings: Settings, mock_api: respx.MockRouter
) -> None:
    assert settings.github_enable_write_tools is False
    post = mock_api.post(url__regex=r".*")
    with pytest.raises(WriteDisabledError):
        await IssueService(client, settings).create_issue(
            i.CreateIssueInput(owner="o", repo="r", title="t")
        )
    with pytest.raises(WriteDisabledError):
        await IssueService(client, settings).add_comment(
            i.AddIssueCommentInput(owner="o", repo="r", issue_number=1, body="b")
        )
    with pytest.raises(WriteDisabledError):
        await RepositoryService(client, settings).create_branch(
            i.CreateBranchInput(owner="o", repo="r", branch="x")
        )
    with pytest.raises(WriteDisabledError):
        await PullRequestService(client, settings).create_pull_request(
            i.CreatePullRequestInput(owner="o", repo="r", title="t", head="h", base="main")
        )
    assert not post.called


async def test_write_operations_when_enabled(
    client: GitHubClient, make_settings: Callable[..., Settings], mock_api: respx.MockRouter
) -> None:
    s = make_settings(github_enable_write_tools=True)
    mock_api.post("/repos/o/r/issues").respond(201, json={"number": 9, "html_url": "u9"})
    mock_api.post("/repos/o/r/issues/9/comments").respond(201, json={"id": 77, "html_url": "c"})
    mock_api.get("/repos/o/r").respond(200, json=repo_json())
    mock_api.get("/repos/o/r/commits/main").respond(200, json={"sha": "deadbeef"})
    ref = mock_api.post("/repos/o/r/git/refs").respond(201, json={"ref": "refs/heads/new"})
    mock_api.post("/repos/o/r/pulls").respond(201, json={"number": 3, "html_url": "p"})

    issues = IssueService(client, s)
    assert (
        await issues.create_issue(
            i.CreateIssueInput(owner="o", repo="r", title="T", labels=["bug"])
        )
    ).number == 9
    assert (
        await issues.add_comment(
            i.AddIssueCommentInput(owner="o", repo="r", issue_number=9, body="hi")
        )
    ).id == 77
    b = await RepositoryService(client, s).create_branch(
        i.CreateBranchInput(owner="o", repo="r", branch="new")
    )
    assert b.sha == "deadbeef"
    assert ref.calls[0].request.content == b'{"ref":"refs/heads/new","sha":"deadbeef"}'
    pr = await PullRequestService(client, s).create_pull_request(
        i.CreatePullRequestInput(owner="o", repo="r", title="T", head="new", base="main")
    )
    assert pr.number == 3
