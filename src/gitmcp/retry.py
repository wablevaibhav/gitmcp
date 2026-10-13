"""Bounded retry with exponential backoff, full jitter and rate-limit awareness."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TypeVar

from gitmcp.errors import GitHubAPIError, NetworkError, RateLimitError

log = logging.getLogger(__name__)
T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0
    max_rate_limit_wait: float = 60.0
    sleep: Callable[[float], Awaitable[None]] = field(default=asyncio.sleep, repr=False)
    uniform: Callable[[float, float], float] = field(default=random.uniform, repr=False)

    def backoff(self, attempt: int) -> float:
        """Full-jitter exponential backoff for retry number ``attempt`` (0-based)."""
        return self.uniform(0.0, min(self.max_delay, self.base_delay * (2**attempt)))


def is_retryable(exc: Exception, *, idempotent: bool) -> bool:
    if isinstance(exc, RateLimitError):
        return True  # GitHub rejected the request outright, so replaying is safe.
    if not idempotent:
        return False  # a timeout/5xx on a POST may have succeeded; never replay it
    if isinstance(exc, NetworkError):
        return True
    return isinstance(exc, GitHubAPIError) and exc.transient


async def call_with_retry(
    op: Callable[[], Awaitable[T]], policy: RetryPolicy, *, idempotent: bool = True
) -> T:
    """Run ``op``, retrying transient failures within the policy's budget."""
    attempt = 0
    while True:
        try:
            return await op()
        except (RateLimitError, NetworkError, GitHubAPIError) as exc:
            if not is_retryable(exc, idempotent=idempotent):
                raise
            if attempt >= policy.max_retries:
                log.warning("retry budget exhausted after %d retries: %s", attempt, exc.message)
                raise
            if isinstance(exc, RateLimitError):
                wait = exc.retry_after if exc.retry_after is not None else policy.backoff(attempt)
                if wait > policy.max_rate_limit_wait:
                    raise RateLimitError(
                        f"GitHub rate limit reached; resets in about {int(wait)}s, "
                        f"longer than the {int(policy.max_rate_limit_wait)}s retry budget.",
                        retry_after=wait,
                        hint="Wait for the limit to reset, or set GITHUB_TOKEN for a higher limit.",
                    ) from exc
                wait += policy.uniform(0.0, 0.25)
            else:
                wait = policy.backoff(attempt)
            log.info(
                "retrying in %.2fs (retry %d/%d): %s",
                wait,
                attempt + 1,
                policy.max_retries,
                exc.message,
            )
            await policy.sleep(wait)
            attempt += 1
