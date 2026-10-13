"""Repository, content and code-search operations."""

from __future__ import annotations

import base64
import binascii
from typing import Any

from gitmcp.config import Settings
from gitmcp.errors import (
    AuthenticationError,
    ContentError,
    GitHubAPIError,
    InputValidationError,
    WriteDisabledError,
)
from gitmcp.github._util import (
    enc,
    enc_path,
    expect_dict,
    expect_list,
    field,
    repo_path,
)
from gitmcp.github.client import RAW_ACCEPT, TEXT_MATCH_ACCEPT, GitHubClient
from gitmcp.schemas import inputs as i
from gitmcp.schemas import outputs as o


def _summary(r: dict[str, Any]) -> dict[str, Any]:
    owner = r.get("owner") if isinstance(r.get("owner"), dict) else {}
    visibility = r.get("visibility") or ("private" if r.get("private") else "public")
    return {
        "full_name": field(r, "full_name", "repository"),
        "owner": (owner or {}).get("login") or str(r["full_name"]).split("/")[0],
        "name": field(r, "name", "repository"),
        "description": r.get("description"),
        "html_url": field(r, "html_url", "repository"),
        "visibility": visibility,
        "language": r.get("language"),
        "stars": r.get("stargazers_count", 0),
        "default_branch": r.get("default_branch"),
    }


class RepositoryService:
    def __init__(self, client: GitHubClient, settings: Settings) -> None:
        self._c = client
        self._s = settings

    async def search_repositories(self, p: i.SearchRepositoriesInput) -> o.RepositorySearchResult:
        q = p.query
        if p.language:
            q += f" language:{p.language}"
        if p.topic:
            q += f" topic:{p.topic}"
        if p.min_stars is not None:
            q += f" stars:>={p.min_stars}"
        resp = await self._c.get(
            "/search/repositories",
            {"q": q, "page": p.page, "per_page": p.per_page, "sort": p.sort, "order": p.order},
        )
        data = expect_dict(resp.data, "repository search")
        items = expect_list(data.get("items", []), "repository search items")
        return o.RepositorySearchResult(
            total_count=data.get("total_count", len(items)),
            incomplete_results=bool(data.get("incomplete_results", False)),
            page=p.page,
            per_page=p.per_page,
            has_next_page=resp.has_next_page,
            items=[o.RepositorySummary(**_summary(r)) for r in items],
        )

    async def get_repository(self, p: i.GetRepositoryInput) -> o.RepositoryDetails:
        resp = await self._c.get(repo_path(p.owner, p.repo))
        r = expect_dict(resp.data, "repository")
        lic = r.get("license")
        return o.RepositoryDetails(
            **_summary(r),
            license=(lic.get("spdx_id") or lic.get("name")) if isinstance(lic, dict) else None,
            forks=r.get("forks_count", 0),
            open_issues=r.get("open_issues_count", 0),
            topics=[t for t in r.get("topics", []) if isinstance(t, str)],
            private=bool(r.get("private", False)),
            archived=bool(r.get("archived", False)),
            fork=bool(r.get("fork", False)),
            clone_url=r.get("clone_url"),
            ssh_url=r.get("ssh_url"),
            api_url=r.get("url"),
            homepage=r.get("homepage") or None,
            created_at=r.get("created_at"),
            updated_at=r.get("updated_at"),
            pushed_at=r.get("pushed_at"),
        )

    async def list_contents(self, p: i.ListContentsInput) -> o.DirectoryListing:
        path = f"{repo_path(p.owner, p.repo)}/contents/{enc_path(p.path)}".rstrip("/")
        resp = await self._c.get(path, {"ref": p.ref})
        raw = resp.data
        entries = [raw] if isinstance(raw, dict) else expect_list(raw, "directory contents")
        return o.DirectoryListing(
            path=p.path,
            ref=p.ref,
            entries=[
                o.ContentEntry(
                    name=field(e, "name", "content entry"),
                    path=field(e, "path", "content entry"),
                    type=e.get("type", "file"),
                    size=e.get("size", 0),
                    sha=e.get("sha"),
                    html_url=e.get("html_url"),
                    api_url=e.get("url"),
                    download_url=e.get("download_url"),
                )
                for e in entries
            ],
        )

    async def get_file_contents(self, p: i.GetFileInput) -> o.FileContents:
        limit = self._s.gitmcp_max_file_bytes
        path = f"{repo_path(p.owner, p.repo)}/contents/{enc_path(p.path)}"
        resp = await self._c.get(path, {"ref": p.ref})
        meta = resp.data
        if isinstance(meta, list):
            raise ContentError(
                f"'{p.path}' is a directory, not a file.",
                hint="Use github_list_repository_contents for directories.",
            )
        meta = expect_dict(meta, "file")
        kind = meta.get("type")
        if kind != "file":
            raise ContentError(f"'{p.path}' is a {kind}, not a regular file.")
        size = meta.get("size", 0)
        if not isinstance(size, int):
            raise GitHubAPIError("Malformed GitHub response: file size is not an integer.")
        if size > limit:
            raise ContentError(
                f"File is {size} bytes, over the {limit}-byte limit.",
                hint="Raise GITMCP_MAX_FILE_BYTES or read a smaller file.",
            )

        if meta.get("encoding") == "base64" and isinstance(meta.get("content"), str):
            try:
                blob = base64.b64decode(meta["content"], validate=False)
            except (binascii.Error, ValueError) as exc:
                raise GitHubAPIError("File content is not valid base64.") from exc
        else:
            # GitHub omits inline content for some files; fetch the raw bytes instead.
            raw = await self._c.get(path, {"ref": p.ref}, accept=RAW_ACCEPT)
            blob = raw.data if isinstance(raw.data, bytes) else b""
        if len(blob) > limit:
            raise ContentError(f"File is over the {limit}-byte limit.")

        if b"\x00" in blob[:8192]:
            raise ContentError(
                f"'{p.path}' looks like a binary file; only text files can be returned.",
                hint=f"Open it in the browser: {meta.get('html_url') or 'GitHub web UI'}",
            )
        try:
            text = blob.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ContentError(
                f"'{p.path}' is not valid UTF-8 text; binary content is unsupported."
            ) from exc
        return o.FileContents(
            path=meta.get("path", p.path),
            ref=p.ref,
            sha=field(meta, "sha", "file"),
            size=size,
            html_url=meta.get("html_url"),
            content=text,
        )

    async def search_code(self, p: i.SearchCodeInput) -> o.CodeSearchResult:
        if not self._c.has_token:
            raise AuthenticationError(
                "GitHub code search requires authentication.",
                hint="Set GITHUB_TOKEN, and scope the query with repo:, org: or user:.",
            )
        resp = await self._c.get(
            "/search/code",
            {"q": p.query, "page": p.page, "per_page": p.per_page},
            accept=TEXT_MATCH_ACCEPT,
        )
        data = expect_dict(resp.data, "code search")
        items = expect_list(data.get("items", []), "code search items")
        out: list[o.CodeSearchItem] = []
        for it in items:
            repo = it.get("repository")
            matches = it.get("text_matches")
            out.append(
                o.CodeSearchItem(
                    name=field(it, "name", "code result"),
                    path=field(it, "path", "code result"),
                    repository=repo.get("full_name", "") if isinstance(repo, dict) else "",
                    html_url=field(it, "html_url", "code result"),
                    sha=it.get("sha"),
                    snippets=[
                        m["fragment"]
                        for m in (matches if isinstance(matches, list) else [])
                        if isinstance(m, dict) and isinstance(m.get("fragment"), str)
                    ],
                )
            )
        return o.CodeSearchResult(
            total_count=data.get("total_count", len(out)),
            incomplete_results=bool(data.get("incomplete_results", False)),
            page=p.page,
            per_page=p.per_page,
            has_next_page=resp.has_next_page,
            items=out,
        )

    # --- write ---------------------------------------------------------------

    async def create_branch(self, p: i.CreateBranchInput) -> o.CreatedBranch:
        if not self._s.github_enable_write_tools:
            raise WriteDisabledError(
                "Write tools are disabled.", hint="Set GITHUB_ENABLE_WRITE_TOOLS=true to enable."
            )
        base = p.from_ref
        if base is None:
            repo = expect_dict((await self._c.get(repo_path(p.owner, p.repo))).data, "repository")
            base = field(repo, "default_branch", "repository")
        if not isinstance(base, str):
            raise InputValidationError("Could not determine the branch to start from.")
        commit = expect_dict(
            (await self._c.get(f"{repo_path(p.owner, p.repo)}/commits/{enc(base)}")).data, "commit"
        )
        sha = field(commit, "sha", "commit")
        created = expect_dict(
            (
                await self._c.post(
                    f"{repo_path(p.owner, p.repo)}/git/refs",
                    {"ref": f"refs/heads/{p.branch}", "sha": sha},
                )
            ).data,
            "created ref",
        )
        return o.CreatedBranch(ref=field(created, "ref", "ref"), sha=sha)
