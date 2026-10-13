"""Small parsing helpers shared by the service modules."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from gitmcp.errors import GitHubAPIError


def expect_dict(data: Any, what: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise GitHubAPIError(f"Malformed GitHub response: expected an object for {what}.")
    return data


def expect_list(data: Any, what: str) -> list[dict[str, Any]]:
    if not isinstance(data, list) or not all(isinstance(i, dict) for i in data):
        raise GitHubAPIError(f"Malformed GitHub response: expected a list of objects for {what}.")
    return data


def field(obj: dict[str, Any], key: str, what: str) -> Any:
    """Required key; a missing one means GitHub's payload is not what we expect."""
    if key not in obj or obj[key] is None:
        raise GitHubAPIError(f"Malformed GitHub response: {what} is missing '{key}'.")
    return obj[key]


def login(user: Any) -> str | None:
    if isinstance(user, dict):
        value = user.get("login")
        return value if isinstance(value, str) else None
    return None


def logins(users: Any) -> list[str]:
    if not isinstance(users, list):
        return []
    return [name for u in users if (name := login(u))]


def truncate(text: str | None, limit: int) -> tuple[str | None, bool]:
    if text is None or len(text) <= limit:
        return text, False
    return text[:limit] + f"\n... [truncated {len(text) - limit} characters]", True


def enc(value: str) -> str:
    """Percent-encode a single URL path segment."""
    return quote(value, safe="")


def enc_path(value: str) -> str:
    """Percent-encode a multi-segment repository path, keeping '/' separators."""
    return quote(value, safe="/")


def repo_path(owner: str, repo: str) -> str:
    return f"/repos/{enc(owner)}/{enc(repo)}"
