"""Authenticated, retrying, size-limited GitHub REST client."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from gitmcp import __version__
from gitmcp.config import Settings
from gitmcp.errors import (
    AuthenticationError,
    AuthorizationError,
    GitHubAPIError,
    InputValidationError,
    NetworkError,
    NotFoundError,
    RateLimitError,
)
from gitmcp.retry import RetryPolicy, call_with_retry

log = logging.getLogger(__name__)

_TRANSIENT_STATUSES = {500, 502, 503, 504}
RAW_ACCEPT = "application/vnd.github.raw+json"
TEXT_MATCH_ACCEPT = "application/vnd.github.text-match+json"


@dataclass(frozen=True)
class GitHubResponse:
    status_code: int
    data: Any
    headers: httpx.Headers

    @property
    def has_next_page(self) -> bool:
        return 'rel="next"' in self.headers.get("link", "")


def _short_error_message(body: bytes) -> str:
    """Pull GitHub's ``message`` field out of an error body, bounded in length."""
    try:
        parsed = json.loads(body)
    except ValueError:
        return ""
    if isinstance(parsed, dict):
        msg = parsed.get("message")
        if isinstance(msg, str):
            return msg[:300]
    return ""


class GitHubClient:
    """Thin async wrapper over ``httpx.AsyncClient`` with typed errors and retries.

    Only GitHub REST paths under the configured API URL can be requested; callers pass
    relative paths, never full URLs.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        policy: RetryPolicy | None = None,
    ) -> None:
        self._settings = settings
        self._has_token = settings.token_value is not None
        self._policy = policy or RetryPolicy(
            max_retries=settings.github_max_retries,
            max_rate_limit_wait=settings.github_max_rate_limit_wait,
        )
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": settings.github_api_version,
            "User-Agent": f"gitmcp/{__version__}",
        }
        if settings.token_value:
            headers["Authorization"] = f"Bearer {settings.token_value}"
        self._http = httpx.AsyncClient(
            base_url=settings.github_api_url,
            headers=headers,
            timeout=httpx.Timeout(settings.github_request_timeout),
            transport=transport,
            follow_redirects=False,
        )

    @property
    def has_token(self) -> bool:
        return self._has_token

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get(
        self, path: str, params: dict[str, Any] | None = None, *, accept: str | None = None
    ) -> GitHubResponse:
        return await self.request("GET", path, params=params, accept=accept)

    async def post(self, path: str, json_body: dict[str, Any]) -> GitHubResponse:
        return await self.request("POST", path, json_body=json_body)

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        accept: str | None = None,
    ) -> GitHubResponse:
        if not path.startswith("/"):
            raise InputValidationError("Internal error: API path must start with '/'.")
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        idempotent = method.upper() in {"GET", "HEAD"}
        return await call_with_retry(
            lambda: self._send_once(method, path, clean, json_body, accept),
            self._policy,
            idempotent=idempotent,
        )

    async def _send_once(
        self,
        method: str,
        path: str,
        params: dict[str, Any],
        json_body: dict[str, Any] | None,
        accept: str | None,
    ) -> GitHubResponse:
        raw = accept == RAW_ACCEPT
        headers = {"Accept": accept} if accept else None
        try:
            async with self._http.stream(
                method, path, params=params, json=json_body, headers=headers
            ) as resp:
                body = await self._read_limited(resp)
                status, resp_headers = resp.status_code, resp.headers
        except httpx.TimeoutException as exc:
            raise NetworkError(
                f"Request to GitHub timed out after {self._settings.github_request_timeout:g}s.",
                hint="Increase GITHUB_REQUEST_TIMEOUT or retry later.",
            ) from exc
        except httpx.TransportError as exc:
            raise NetworkError(
                f"Could not reach GitHub ({type(exc).__name__}).",
                hint="Check network connectivity and GITHUB_API_URL.",
            ) from exc

        log.debug("%s %s -> %s", method, path, status)
        if status >= 400:
            raise self._map_error(status, resp_headers, body)

        if raw:
            return GitHubResponse(status, body, resp_headers)
        if status == 204 or not body:
            return GitHubResponse(status, None, resp_headers)
        try:
            data = json.loads(body)
        except ValueError as exc:
            raise GitHubAPIError(
                "GitHub returned a response that is not valid JSON.", status_code=status
            ) from exc
        return GitHubResponse(status, data, resp_headers)

    async def _read_limited(self, resp: httpx.Response) -> bytes:
        limit = self._settings.gitmcp_max_response_bytes
        declared = resp.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise GitHubAPIError(
                f"GitHub response is larger than the {limit}-byte limit.",
                status_code=resp.status_code,
                hint="Request a narrower slice (smaller page, specific path).",
            )
        chunks: list[bytes] = []
        total = 0
        async for chunk in resp.aiter_bytes():
            total += len(chunk)
            if total > limit:
                raise GitHubAPIError(
                    f"GitHub response exceeded the {limit}-byte limit.",
                    status_code=resp.status_code,
                    hint="Request a narrower slice (smaller page, specific path).",
                )
            chunks.append(chunk)
        return b"".join(chunks)

    def _map_error(self, status: int, headers: httpx.Headers, body: bytes) -> Exception:
        detail = _short_error_message(body)
        suffix = f" GitHub said: {detail}" if detail else ""

        if status == 401:
            return AuthenticationError(
                "GitHub rejected the credentials." + suffix,
                hint="Check that GITHUB_TOKEN is set, valid and not expired.",
            )

        if status in (403, 429):
            retry_after = self._rate_limit_wait(headers)
            lowered = detail.lower()
            limited = (
                status == 429
                or retry_after is not None
                or "rate limit" in lowered
                or "abuse" in lowered
            )
            if limited:
                return RateLimitError(
                    "GitHub rate limit reached.",
                    retry_after=retry_after,
                    hint="Wait and retry, or authenticate with GITHUB_TOKEN for higher limits.",
                )
            return AuthorizationError(
                "The token is not allowed to perform this request." + suffix,
                hint="Grant the fine-grained token access to this repository with the "
                "needed permission (e.g. Contents/Issues/Pull requests: read).",
            )

        if status == 404:
            hint = (
                "Check owner, repo, path and ref. Private resources also return 404 "
                "when the token lacks access."
            )
            if not self._has_token:
                hint += " No GITHUB_TOKEN is configured, so only public data is visible."
            return NotFoundError("Resource not found." + suffix, hint=hint)

        if status in (400, 409, 422):
            return InputValidationError("GitHub rejected the request." + suffix)

        if status in _TRANSIENT_STATUSES:
            return GitHubAPIError(
                f"GitHub returned a temporary server error (HTTP {status}).",
                status_code=status,
                transient=True,
                hint="This is usually transient; retry shortly.",
            )

        return GitHubAPIError(
            f"Unexpected response from GitHub (HTTP {status})." + suffix, status_code=status
        )

    @staticmethod
    def _rate_limit_wait(headers: httpx.Headers) -> float | None:
        """Seconds to wait, from ``Retry-After`` or ``x-ratelimit-reset`` when exhausted."""
        retry_after = headers.get("retry-after")
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                pass
        if headers.get("x-ratelimit-remaining") == "0":
            reset = headers.get("x-ratelimit-reset")
            if reset:
                try:
                    return max(0.0, float(reset) - time.time()) + 1.0
                except ValueError:
                    pass
            return 60.0
        return None
