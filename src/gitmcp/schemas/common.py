"""Shared validated types and base classes for tool inputs."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_REF_BAD_CHARS = re.compile(r"[\x00-\x20\x7f~^:?*\[\\]")
_USER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(?:\[bot\])?$")


def _check_owner(v: str) -> str:
    if not _OWNER_RE.match(v):
        raise ValueError(
            "owner must be 1-39 letters, digits or hyphens, starting with alphanumeric"
        )
    return v


def _check_repo(v: str) -> str:
    if not _REPO_RE.match(v) or v in {".", ".."}:
        raise ValueError("repo must be 1-100 letters, digits, '.', '_' or '-'")
    return v


def _check_ref(v: str) -> str:
    if (
        not v
        or len(v) > 255
        or _REF_BAD_CHARS.search(v)
        or ".." in v
        or "@{" in v
        or v.startswith(("/", "-"))
        or v.endswith(("/", ".", ".lock"))
        or "//" in v
        or v == "@"
    ):
        raise ValueError("not a valid Git reference (branch, tag or commit SHA)")
    return v


def _check_path(v: str) -> str:
    v = v.strip("/")
    if len(v) > 1024 or "\x00" in v or "\\" in v:
        raise ValueError("path is too long or contains forbidden characters")
    if any(seg in ("..", ".") for seg in v.split("/")):
        raise ValueError("path must not contain '.' or '..' segments")
    return v


def _check_user(v: str) -> str:
    if not _USER_RE.match(v):
        raise ValueError("not a valid GitHub username")
    return v


Owner = Annotated[
    str,
    AfterValidator(_check_owner),
    Field(description="Repository owner (user or organization login).", examples=["octocat"]),
]
Repo = Annotated[
    str,
    AfterValidator(_check_repo),
    Field(description="Repository name, without the owner.", examples=["Hello-World"]),
]
GitRef = Annotated[
    str,
    AfterValidator(_check_ref),
    Field(description="Branch name, tag or commit SHA."),
]
RepoPath = Annotated[
    str,
    AfterValidator(_check_path),
    Field(description="Path inside the repository, no leading slash. Empty means the root."),
]
Username = Annotated[str, AfterValidator(_check_user), Field(description="GitHub username.")]
Page = Annotated[int, Field(ge=1, le=1000, description="1-based page number.")]
PerPage = Annotated[int, Field(ge=1, le=100, description="Results per page (1-100).")]
IssueNumber = Annotated[int, Field(ge=1, le=2_000_000_000, description="Issue or PR number.")]
Label = Annotated[str, StringConstraints(min_length=1, max_length=50, strip_whitespace=True)]


class ToolInput(BaseModel):
    """Base for every tool input: unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RepoInput(ToolInput):
    owner: Owner
    repo: Repo


class PagedInput(ToolInput):
    page: Page = 1
    per_page: PerPage = 30
