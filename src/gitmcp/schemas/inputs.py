"""Tool input models. Each is strict (``extra="forbid"``) and self-describing."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from gitmcp.schemas.common import (
    GitRef,
    IssueNumber,
    Label,
    PagedInput,
    RepoInput,
    RepoPath,
    Username,
)

# --- repositories -----------------------------------------------------------------


class SearchRepositoriesInput(PagedInput):
    query: str = Field(
        min_length=1,
        max_length=256,
        description="GitHub repository search query, e.g. 'web framework' or 'user:octocat'.",
    )
    language: Annotated[str, StringConstraints(max_length=50)] | None = Field(
        default=None, description="Only repositories whose primary language matches."
    )
    topic: Annotated[str, StringConstraints(max_length=50)] | None = Field(
        default=None, description="Only repositories tagged with this topic."
    )
    min_stars: int | None = Field(default=None, ge=0, description="Minimum star count.")
    sort: Literal["stars", "forks", "help-wanted-issues", "updated"] | None = Field(
        default=None, description="Sort field. Omit for best match."
    )
    order: Literal["asc", "desc"] = "desc"

    @model_validator(mode="after")
    def _no_whitespace_in_qualifiers(self) -> SearchRepositoriesInput:
        for name in ("language", "topic"):
            val = getattr(self, name)
            if val is not None and (not val.strip() or any(c.isspace() for c in val)):
                raise ValueError(f"{name} must be a single non-empty word")
        return self


class GetRepositoryInput(RepoInput):
    """Identify a repository."""


class ListContentsInput(RepoInput):
    path: RepoPath = ""
    ref: GitRef | None = Field(default=None, description="Branch, tag or SHA. Defaults to HEAD.")


class GetFileInput(RepoInput):
    path: RepoPath = Field(min_length=1, description="File path inside the repository.")
    ref: GitRef | None = Field(default=None, description="Branch, tag or SHA. Defaults to HEAD.")


class SearchCodeInput(PagedInput):
    query: str = Field(
        min_length=1,
        max_length=256,
        description=(
            "Code search query. GitHub requires at least one qualifier such as "
            "'repo:owner/name', 'org:name', 'user:name' (e.g. 'parse_config repo:octocat/x')."
        ),
    )


# --- issues -----------------------------------------------------------------------


class ListIssuesInput(RepoInput, PagedInput):
    state: Literal["open", "closed", "all"] = "open"
    labels: list[Label] | None = Field(
        default=None, max_length=20, description="Only issues having all of these labels."
    )
    assignee: Username | Literal["none", "*"] | None = Field(
        default=None, description="Username, 'none' (unassigned) or '*' (any)."
    )
    include_pull_requests: bool = Field(
        default=False,
        description="GitHub's issues API also returns PRs; they are hidden unless true. "
        "Items always carry is_pull_request.",
    )


class GetIssueInput(RepoInput):
    issue_number: IssueNumber


class ListIssueCommentsInput(RepoInput, PagedInput):
    issue_number: IssueNumber


# --- pull requests ----------------------------------------------------------------


class ListPullRequestsInput(RepoInput, PagedInput):
    state: Literal["open", "closed", "all"] = "open"
    base: GitRef | None = Field(default=None, description="Filter by base branch name.")
    head: (
        Annotated[str, StringConstraints(min_length=1, max_length=300, pattern=r"^[^\s]+$")] | None
    ) = Field(default=None, description="Filter by head, formatted 'owner:branch'.")


class GetPullRequestInput(RepoInput):
    pull_number: IssueNumber


class ListPullRequestFilesInput(RepoInput, PagedInput):
    pull_number: IssueNumber


class ListPullRequestReviewsInput(RepoInput, PagedInput):
    pull_number: IssueNumber


# --- commits ----------------------------------------------------------------------


class ListCommitsInput(RepoInput, PagedInput):
    ref: GitRef | None = Field(default=None, description="Branch, tag or SHA to list from.")
    path: RepoPath | None = Field(default=None, description="Only commits touching this path.")
    author: Username | None = Field(default=None, description="Only commits by this GitHub user.")


class GetCommitInput(RepoInput):
    ref: GitRef = Field(description="Commit SHA, branch or tag.")


# --- write tools ------------------------------------------------------------------

_Title = Annotated[str, StringConstraints(min_length=1, max_length=256, strip_whitespace=True)]
_Body = Annotated[str, StringConstraints(max_length=65_536)]


class CreateIssueInput(RepoInput):
    title: _Title
    body: _Body | None = None
    labels: list[Label] | None = Field(default=None, max_length=20)
    assignees: list[Username] | None = Field(default=None, max_length=10)


class AddIssueCommentInput(RepoInput):
    issue_number: IssueNumber
    body: Annotated[str, StringConstraints(min_length=1, max_length=65_536)]


class CreateBranchInput(RepoInput):
    branch: GitRef = Field(description="Name of the new branch.")
    from_ref: GitRef | None = Field(
        default=None,
        description="Branch, tag or SHA to branch from. Defaults to the default branch.",
    )


class CreatePullRequestInput(RepoInput):
    title: _Title
    head: Annotated[str, StringConstraints(min_length=1, max_length=300, pattern=r"^[^\s]+$")] = (
        Field(description="Branch with the changes ('branch' or 'owner:branch' for forks).")
    )
    base: GitRef = Field(description="Branch to merge into.")
    body: _Body | None = None
    draft: bool = False
