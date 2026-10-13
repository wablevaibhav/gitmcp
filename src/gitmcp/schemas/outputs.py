"""Tool output models (also the MCP structured-output schemas)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Page(BaseModel):
    page: int
    per_page: int
    has_next_page: bool = Field(description="True when another page is available.")


# --- repositories -----------------------------------------------------------------


class RepositorySummary(BaseModel):
    full_name: str
    owner: str
    name: str
    description: str | None = None
    html_url: str
    visibility: str
    language: str | None = None
    stars: int = 0
    default_branch: str | None = None


class RepositorySearchResult(Page):
    total_count: int
    incomplete_results: bool = False
    items: list[RepositorySummary]


class RepositoryDetails(RepositorySummary):
    license: str | None = None
    forks: int = 0
    open_issues: int = 0
    topics: list[str] = []
    private: bool = False
    archived: bool = False
    fork: bool = False
    clone_url: str | None = None
    ssh_url: str | None = None
    api_url: str | None = None
    homepage: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    pushed_at: str | None = None


class ContentEntry(BaseModel):
    name: str
    path: str
    type: str = Field(description="file, dir, symlink or submodule.")
    size: int = 0
    sha: str | None = None
    html_url: str | None = None
    api_url: str | None = None
    download_url: str | None = None


class DirectoryListing(BaseModel):
    path: str
    ref: str | None
    entries: list[ContentEntry]


class FileContents(BaseModel):
    path: str
    ref: str | None
    sha: str
    size: int
    html_url: str | None = None
    content: str
    encoding: str = "utf-8"


class CodeSearchItem(BaseModel):
    name: str
    path: str
    repository: str
    html_url: str
    sha: str | None = None
    snippets: list[str] = Field(default=[], description="Matching text fragments, when available.")


class CodeSearchResult(Page):
    total_count: int
    incomplete_results: bool = False
    items: list[CodeSearchItem]


# --- issues / PRs -----------------------------------------------------------------


class IssueSummary(BaseModel):
    number: int
    title: str
    state: str
    is_pull_request: bool
    author: str | None = None
    labels: list[str] = []
    assignees: list[str] = []
    comments: int = 0
    created_at: str | None = None
    updated_at: str | None = None
    html_url: str


class IssueList(Page):
    items: list[IssueSummary]


class IssueDetails(IssueSummary):
    body: str | None = None
    body_truncated: bool = False
    milestone: str | None = None
    closed_at: str | None = None
    locked: bool = False


class IssueComment(BaseModel):
    id: int
    author: str | None = None
    body: str
    body_truncated: bool = False
    created_at: str | None = None
    updated_at: str | None = None
    html_url: str


class IssueCommentList(Page):
    items: list[IssueComment]


class PullRequestSummary(BaseModel):
    number: int
    title: str
    state: str
    draft: bool = False
    merged: bool = False
    author: str | None = None
    head: str
    base: str
    created_at: str | None = None
    updated_at: str | None = None
    html_url: str


class PullRequestList(Page):
    items: list[PullRequestSummary]


class PullRequestDetails(PullRequestSummary):
    body: str | None = None
    body_truncated: bool = False
    head_sha: str | None = None
    base_sha: str | None = None
    mergeable: bool | None = Field(default=None, description="None while GitHub computes it.")
    mergeable_state: str | None = None
    merged_by: str | None = None
    merged_at: str | None = None
    closed_at: str | None = None
    commits: int = 0
    additions: int = 0
    deletions: int = 0
    changed_files: int = 0
    comments: int = 0
    review_comments: int = 0
    requested_reviewers: list[str] = []
    labels: list[str] = []


class ChangedFile(BaseModel):
    filename: str
    status: str
    additions: int = 0
    deletions: int = 0
    changes: int = 0
    previous_filename: str | None = None
    patch: str | None = Field(
        default=None, description="Unified diff; absent for binary/large files."
    )
    patch_truncated: bool = False


class ChangedFileList(Page):
    items: list[ChangedFile]


class Review(BaseModel):
    id: int
    author: str | None = None
    state: str = Field(description="APPROVED, CHANGES_REQUESTED, COMMENTED, DISMISSED or PENDING.")
    body: str = ""
    body_truncated: bool = False
    submitted_at: str | None = None
    commit_id: str | None = None
    html_url: str | None = None


class ReviewList(Page):
    items: list[Review]


# --- commits ----------------------------------------------------------------------


class CommitSummary(BaseModel):
    sha: str
    message: str
    author_name: str | None = None
    author_login: str | None = None
    date: str | None = None
    html_url: str


class CommitList(Page):
    items: list[CommitSummary]


class CommitStats(BaseModel):
    additions: int = 0
    deletions: int = 0
    total: int = 0


class CommitDetails(CommitSummary):
    committer_name: str | None = None
    parents: list[str] = []
    stats: CommitStats | None = None
    files: list[ChangedFile] = []
    files_truncated: bool = Field(default=False, description="True when file list was capped.")


# --- write results ----------------------------------------------------------------


class CreatedIssue(BaseModel):
    number: int
    html_url: str


class CreatedComment(BaseModel):
    id: int
    html_url: str


class CreatedBranch(BaseModel):
    ref: str
    sha: str


class CreatedPullRequest(BaseModel):
    number: int
    html_url: str
