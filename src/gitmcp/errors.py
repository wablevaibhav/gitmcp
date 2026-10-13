"""Typed application errors.

Every error carries a short, safe ``message`` (no tokens, no response bodies) and an
optional actionable ``hint``. ``safe_message()`` is what MCP clients see.
"""

from __future__ import annotations


class GitMCPError(Exception):
    """Base class for all expected GitMCP failures."""

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def safe_message(self) -> str:
        text = f"{type(self).__name__}: {self.message}"
        return f"{text} Hint: {self.hint}" if self.hint else text


class InputValidationError(GitMCPError):
    """Input rejected locally or by GitHub (HTTP 422 / 400)."""


class AuthenticationError(GitMCPError):
    """Missing, invalid or expired credentials (HTTP 401)."""


class AuthorizationError(GitMCPError):
    """Authenticated but not permitted (HTTP 403 without rate-limit signals)."""


class WriteDisabledError(AuthorizationError):
    """A write operation was requested while write tools are disabled."""


class NotFoundError(GitMCPError):
    """Repository, file, issue, PR, ref ... does not exist or is not visible."""


class RateLimitError(GitMCPError):
    """GitHub rate limit hit. ``retry_after`` is seconds until retrying may succeed."""

    def __init__(
        self, message: str, *, retry_after: float | None = None, hint: str | None = None
    ) -> None:
        super().__init__(message, hint=hint)
        self.retry_after = retry_after


class NetworkError(GitMCPError):
    """Timeouts and connection failures."""


class GitHubAPIError(GitMCPError):
    """Unexpected GitHub API response (odd status, malformed or oversized body)."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        hint: str | None = None,
        transient: bool = False,
    ) -> None:
        super().__init__(message, hint=hint)
        self.status_code = status_code
        self.transient = transient


class ContentError(GitMCPError):
    """File content cannot be returned (binary, too large, not a file)."""
