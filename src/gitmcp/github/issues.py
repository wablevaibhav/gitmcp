"""Issue and issue-comment operations."""

from __future__ import annotations

from typing import Any

from gitmcp.config import Settings
from gitmcp.errors import WriteDisabledError
from gitmcp.github._util import (
    expect_dict,
    expect_list,
    field,
    login,
    logins,
    repo_path,
    truncate,
)
from gitmcp.github.client import GitHubClient
from gitmcp.schemas import inputs as i
from gitmcp.schemas import outputs as o


def _label_names(labels: Any) -> list[str]:
    if not isinstance(labels, list):
        return []
    names: list[str] = []
    for lbl in labels:
        name = lbl.get("name") if isinstance(lbl, dict) else lbl
        if isinstance(name, str):
            names.append(name)
    return names


def _issue_fields(it: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": field(it, "number", "issue"),
        "title": field(it, "title", "issue"),
        "state": field(it, "state", "issue"),
        "is_pull_request": "pull_request" in it,
        "author": login(it.get("user")),
        "labels": _label_names(it.get("labels")),
        "assignees": logins(it.get("assignees")),
        "comments": it.get("comments", 0),
        "created_at": it.get("created_at"),
        "updated_at": it.get("updated_at"),
        "html_url": field(it, "html_url", "issue"),
    }


class IssueService:
    def __init__(self, client: GitHubClient, settings: Settings) -> None:
        self._c = client
        self._s = settings

    def _require_writes(self) -> None:
        if not self._s.github_enable_write_tools:
            raise WriteDisabledError(
                "Write tools are disabled.", hint="Set GITHUB_ENABLE_WRITE_TOOLS=true to enable."
            )

    async def list_issues(self, p: i.ListIssuesInput) -> o.IssueList:
        resp = await self._c.get(
            f"{repo_path(p.owner, p.repo)}/issues",
            {
                "state": p.state,
                "labels": ",".join(p.labels) if p.labels else None,
                "assignee": p.assignee,
                "page": p.page,
                "per_page": p.per_page,
            },
        )
        items = [_issue_fields(it) for it in expect_list(resp.data, "issues")]
        if not p.include_pull_requests:
            items = [it for it in items if not it["is_pull_request"]]
        return o.IssueList(
            page=p.page,
            per_page=p.per_page,
            has_next_page=resp.has_next_page,
            items=[o.IssueSummary(**it) for it in items],
        )

    async def get_issue(self, p: i.GetIssueInput) -> o.IssueDetails:
        resp = await self._c.get(f"{repo_path(p.owner, p.repo)}/issues/{p.issue_number}")
        it = expect_dict(resp.data, "issue")
        body, cut = truncate(it.get("body"), self._s.gitmcp_max_comment_chars * 2)
        milestone = it.get("milestone")
        return o.IssueDetails(
            **_issue_fields(it),
            body=body,
            body_truncated=cut,
            milestone=milestone.get("title") if isinstance(milestone, dict) else None,
            closed_at=it.get("closed_at"),
            locked=bool(it.get("locked", False)),
        )

    async def list_comments(self, p: i.ListIssueCommentsInput) -> o.IssueCommentList:
        resp = await self._c.get(
            f"{repo_path(p.owner, p.repo)}/issues/{p.issue_number}/comments",
            {"page": p.page, "per_page": p.per_page},
        )
        out: list[o.IssueComment] = []
        for c in expect_list(resp.data, "comments"):
            body, cut = truncate(c.get("body") or "", self._s.gitmcp_max_comment_chars)
            out.append(
                o.IssueComment(
                    id=field(c, "id", "comment"),
                    author=login(c.get("user")),
                    body=body or "",
                    body_truncated=cut,
                    created_at=c.get("created_at"),
                    updated_at=c.get("updated_at"),
                    html_url=field(c, "html_url", "comment"),
                )
            )
        return o.IssueCommentList(
            page=p.page, per_page=p.per_page, has_next_page=resp.has_next_page, items=out
        )

    # --- write ---------------------------------------------------------------

    async def create_issue(self, p: i.CreateIssueInput) -> o.CreatedIssue:
        self._require_writes()
        payload: dict[str, Any] = {"title": p.title}
        if p.body is not None:
            payload["body"] = p.body
        if p.labels:
            payload["labels"] = p.labels
        if p.assignees:
            payload["assignees"] = p.assignees
        data = expect_dict(
            (await self._c.post(f"{repo_path(p.owner, p.repo)}/issues", payload)).data, "issue"
        )
        return o.CreatedIssue(
            number=field(data, "number", "issue"), html_url=field(data, "html_url", "issue")
        )

    async def add_comment(self, p: i.AddIssueCommentInput) -> o.CreatedComment:
        self._require_writes()
        data = expect_dict(
            (
                await self._c.post(
                    f"{repo_path(p.owner, p.repo)}/issues/{p.issue_number}/comments",
                    {"body": p.body},
                )
            ).data,
            "comment",
        )
        return o.CreatedComment(
            id=field(data, "id", "comment"), html_url=field(data, "html_url", "comment")
        )
