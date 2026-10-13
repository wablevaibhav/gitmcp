"""Commit and history operations."""

from __future__ import annotations

from typing import Any

from gitmcp.config import Settings
from gitmcp.github._util import enc, expect_dict, expect_list, field, login, repo_path
from gitmcp.github.client import GitHubClient
from gitmcp.github.pull_requests import changed_file
from gitmcp.schemas import inputs as i
from gitmcp.schemas import outputs as o

_MAX_FILES = 100


def _summary(c: dict[str, Any]) -> dict[str, Any]:
    commit = c.get("commit")
    commit = commit if isinstance(commit, dict) else {}
    author = commit.get("author")
    author = author if isinstance(author, dict) else {}
    return {
        "sha": field(c, "sha", "commit"),
        "message": str(commit.get("message", "")),
        "author_name": author.get("name"),
        "author_login": login(c.get("author")),
        "date": author.get("date"),
        "html_url": field(c, "html_url", "commit"),
    }


class CommitService:
    def __init__(self, client: GitHubClient, settings: Settings) -> None:
        self._c = client
        self._s = settings

    async def list_commits(self, p: i.ListCommitsInput) -> o.CommitList:
        resp = await self._c.get(
            f"{repo_path(p.owner, p.repo)}/commits",
            {
                "sha": p.ref,
                "path": p.path or None,
                "author": p.author,
                "page": p.page,
                "per_page": p.per_page,
            },
        )
        return o.CommitList(
            page=p.page,
            per_page=p.per_page,
            has_next_page=resp.has_next_page,
            items=[o.CommitSummary(**_summary(c)) for c in expect_list(resp.data, "commits")],
        )

    async def get_commit(self, p: i.GetCommitInput) -> o.CommitDetails:
        resp = await self._c.get(f"{repo_path(p.owner, p.repo)}/commits/{enc(p.ref)}")
        c = expect_dict(resp.data, "commit")
        commit = c.get("commit")
        commit = commit if isinstance(commit, dict) else {}
        committer = commit.get("committer")
        committer = committer if isinstance(committer, dict) else {}
        stats = c.get("stats")
        raw_files = expect_list(c.get("files", []), "commit files")
        limit = self._s.gitmcp_max_patch_chars
        return o.CommitDetails(
            **_summary(c),
            committer_name=committer.get("name"),
            parents=[
                par["sha"]
                for par in c.get("parents", [])
                if isinstance(par, dict) and isinstance(par.get("sha"), str)
            ],
            stats=o.CommitStats(**{k: stats.get(k, 0) for k in ("additions", "deletions", "total")})
            if isinstance(stats, dict)
            else None,
            files=[changed_file(f, limit) for f in raw_files[:_MAX_FILES]],
            files_truncated=len(raw_files) > _MAX_FILES,
        )
