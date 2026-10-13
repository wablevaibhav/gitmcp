from __future__ import annotations

import pytest

from gitmcp.errors import AuthenticationError, GitHubAPIError, NetworkError, RateLimitError
from gitmcp.retry import RetryPolicy, call_with_retry, is_retryable


def test_backoff_is_bounded_and_exponential() -> None:
    p = RetryPolicy(base_delay=1.0, max_delay=5.0, uniform=lambda _lo, hi: hi)
    assert [p.backoff(n) for n in range(5)] == [1.0, 2.0, 4.0, 5.0, 5.0]


def test_real_jitter_stays_in_range() -> None:
    p = RetryPolicy(base_delay=1.0, max_delay=5.0)
    for n in range(6):
        assert 0.0 <= p.backoff(n) <= min(5.0, 2**n)


def test_retryable_classification() -> None:
    assert is_retryable(RateLimitError("x"), idempotent=False)
    assert is_retryable(NetworkError("x"), idempotent=True)
    assert not is_retryable(NetworkError("x"), idempotent=False)
    assert is_retryable(GitHubAPIError("x", transient=True), idempotent=True)
    assert not is_retryable(GitHubAPIError("x", transient=False), idempotent=True)
    assert not is_retryable(AuthenticationError("x"), idempotent=True)


async def test_non_retryable_raises_immediately() -> None:
    calls = 0

    async def op() -> None:
        nonlocal calls
        calls += 1
        raise AuthenticationError("no")

    with pytest.raises(AuthenticationError):
        await call_with_retry(op, RetryPolicy(max_retries=5))
    assert calls == 1


async def test_zero_retries_means_single_attempt() -> None:
    calls = 0

    async def op() -> None:
        nonlocal calls
        calls += 1
        raise NetworkError("down")

    with pytest.raises(NetworkError):
        await call_with_retry(op, RetryPolicy(max_retries=0))
    assert calls == 1
