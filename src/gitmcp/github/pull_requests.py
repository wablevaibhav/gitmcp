"""Pull request operations."""

from __future__ import annotations

from typing import Any

from gitmcp.config import Settings
from gitmcp.errors import GitHubAPIError, WriteDisabledError
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


def changed_file(f: dict[str, Any], patch_limit: int) -> o.ChangedFile:
    patch, cut = truncate(f.get("patch"), patch_limit)
    return o.ChangedFile(
        filename=field(f, "filename", "file"),
        status=f.get("status", "modified"),
        additions=f.get("additions", 0),
        deletions=f.get("deletions", 0),
        changes=f.get("changes", 0),
        previous_filename=f.get("previous_filename"),
        patch=patch,
        patch_truncated=cut,
    )


def _ref_label(ref: Any, what: str) -> str:
    if isinstance(ref, dict):
        for key in ("label", "ref"):
            value = ref.get(key)
            if isinstance(value, str):
                return value
    raise GitHubAPIError(f"Malformed GitHub response: pull request is missing '{what}'.")


def _summary(pr: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": field(pr, "number", "pull request"),
        "title": field(pr, "title", "pull request"),
        "state": field(pr, "state", "pull request"),
        "draft": bool(pr.get("draft", False)),
        "merged": bool(pr.get("merged", False) or pr.get("merged_at")),
        "author": login(pr.get("user")),
        "head": _ref_label(pr.get("head"), "head"),
        "base": _ref_label(pr.get("base"), "base"),
        "created_at": pr.get("created_at"),
        "updated_at": pr.get("updated_at"),
        "html_url": field(pr, "html_url", "pull request"),
    }


class PullRequestService:
    def __init__(self, client: GitHubClient, settings: Settings) -> None:
        self._c = client
        self._s = settings

    async def list_pull_requests(self, p: i.ListPullRequestsInput) -> o.PullRequestList:
        resp = await self._c.get(
            f"{repo_path(p.owner, p.repo)}/pulls",
            {
                "state": p.state,
                "base": p.base,
                "head": p.head,
                "page": p.page,
                "per_page": p.per_page,
            },
        )
        return o.PullRequestList(
            page=p.page,
            per_page=p.per_page,
            has_next_page=resp.has_next_page,
            items=[o.PullRequestSummary(**_summary(pr)) for pr in expect_list(resp.data, "pulls")],
        )

    async def get_pull_request(self, p: i.GetPullRequestInput) -> o.PullRequestDetails:
        resp = await self._c.get(f"{repo_path(p.owner, p.repo)}/pulls/{p.pull_number}")
        pr = expect_dict(resp.data, "pull request")
        body, cut = truncate(pr.get("body"), self._s.gitmcp_max_comment_chars * 2)
        head, base = pr.get("head"), pr.get("base")
        return o.PullRequestDetails(
            **_summary(pr),
            body=body,
            body_truncated=cut,
            head_sha=head.get("sha") if isinstance(head, dict) else None,
            base_sha=base.get("sha") if isinstance(base, dict) else None,
            mergeable=pr.get("mergeable"),
            mergeable_state=pr.get("mergeable_state"),
            merged_by=login(pr.get("merged_by")),
            merged_at=pr.get("merged_at"),
            closed_at=pr.get("closed_at"),
            commits=pr.get("commits", 0),
            additions=pr.get("additions", 0),
            deletions=pr.get("deletions", 0),
            changed_files=pr.get("changed_files", 0),
            comments=pr.get("comments", 0),
            review_comments=pr.get("review_comments", 0),
            requested_reviewers=logins(pr.get("requested_reviewers")),
            labels=[
                lbl["name"]
                for lbl in pr.get("labels", [])
                if isinstance(lbl, dict) and isinstance(lbl.get("name"), str)
            ],
        )

    async def list_files(self, p: i.ListPullRequestFilesInput) -> o.ChangedFileList:
        resp = await self._c.get(
            f"{repo_path(p.owner, p.repo)}/pulls/{p.pull_number}/files",
            {"page": p.page, "per_page": p.per_page},
        )
        limit = self._s.gitmcp_max_patch_chars
        return o.ChangedFileList(
            page=p.page,
            per_page=p.per_page,
            has_next_page=resp.has_next_page,
            items=[changed_file(f, limit) for f in expect_list(resp.data, "pull request files")],
        )

    async def list_reviews(self, p: i.ListPullRequestReviewsInput) -> o.ReviewList:
        resp = await self._c.get(
            f"{repo_path(p.owner, p.repo)}/pulls/{p.pull_number}/reviews",
            {"page": p.page, "per_page": p.per_page},
        )
        out: list[o.Review] = []
        for r in expect_list(resp.data, "reviews"):
            body, cut = truncate(r.get("body") or "", self._s.gitmcp_max_comment_chars)
            out.append(
                o.Review(
                    id=field(r, "id", "review"),
                    author=login(r.get("user")),
                    state=field(r, "state", "review"),
                    body=body or "",
                    body_truncated=cut,
                    submitted_at=r.get("submitted_at"),
                    commit_id=r.get("commit_id"),
                    html_url=r.get("html_url"),
                )
            )
        return o.ReviewList(
            page=p.page, per_page=p.per_page, has_next_page=resp.has_next_page, items=out
        )

    # --- write ---------------------------------------------------------------

    async def create_pull_request(self, p: i.CreatePullRequestInput) -> o.CreatedPullRequest:
        if not self._s.github_enable_write_tools:
            raise WriteDisabledError(
                "Write tools are disabled.", hint="Set GITHUB_ENABLE_WRITE_TOOLS=true to enable."
            )
        payload: dict[str, Any] = {
            "title": p.title,
            "head": p.head,
            "base": p.base,
            "draft": p.draft,
        }
        if p.body is not None:
            payload["body"] = p.body
        data = expect_dict(
            (await self._c.post(f"{repo_path(p.owner, p.repo)}/pulls", payload)).data,
            "pull request",
        )
        return o.CreatedPullRequest(
            number=field(data, "number", "pull request"),
            html_url=field(data, "html_url", "pull request"),
        )
