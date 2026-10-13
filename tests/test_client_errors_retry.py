"""HTTP error mapping and retry policy, exercised through the real GitHubClient."""

from __future__ import annotations

import time

import httpx
import pytest
import respx

from gitmcp.errors import (
    AuthenticationError,
    AuthorizationError,
    GitHubAPIError,
    InputValidationError,
    NetworkError,
    NotFoundError,
    RateLimitError,
)
from gitmcp.github.client import GitHubClient

from .conftest import Sleeper


async def test_success_sends_auth_and_version_headers(
    client: GitHubClient, mock_api: respx.MockRouter
) -> None:
    route = mock_api.get("/user").respond(200, json={"login": "x"})
    resp = await client.get("/user")
    assert resp.data == {"login": "x"}
    sent = route.calls[0].request.headers
    assert sent["authorization"].startswith("Bearer ghp_")
    assert sent["x-github-api-version"] == "2022-11-28"


async def test_no_auth_header_without_token(make_settings, policy, mock_api) -> None:  # type: ignore[no-untyped-def]
    c = GitHubClient(make_settings(github_token=None), policy=policy)
    route = mock_api.get("/x").respond(200, json={})
    await c.get("/x")
    assert "authorization" not in route.calls[0].request.headers
    await c.aclose()


async def test_401_not_retried(
    client: GitHubClient, mock_api: respx.MockRouter, sleeper: Sleeper
) -> None:
    route = mock_api.get("/x").respond(401, json={"message": "Bad credentials"})
    with pytest.raises(AuthenticationError):
        await client.get("/x")
    assert route.call_count == 1
    assert sleeper.calls == []


async def test_403_permission_not_retried(client: GitHubClient, mock_api: respx.MockRouter) -> None:
    route = mock_api.get("/x").respond(
        403, json={"message": "Resource not accessible by personal access token"}
    )
    with pytest.raises(AuthorizationError) as ei:
        await client.get("/x")
    assert route.call_count == 1
    assert "permission" in (ei.value.hint or "").lower()


async def test_404_not_retried(client: GitHubClient, mock_api: respx.MockRouter) -> None:
    route = mock_api.get("/x").respond(404, json={"message": "Not Found"})
    with pytest.raises(NotFoundError):
        await client.get("/x")
    assert route.call_count == 1


async def test_422_is_validation_error(client: GitHubClient, mock_api: respx.MockRouter) -> None:
    mock_api.get("/x").respond(422, json={"message": "Validation Failed"})
    with pytest.raises(InputValidationError):
        await client.get("/x")


async def test_429_retry_after_respected(
    client: GitHubClient, mock_api: respx.MockRouter, sleeper: Sleeper
) -> None:
    route = mock_api.get("/x").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}, json={"message": "slow down"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    resp = await client.get("/x")
    assert resp.data == {"ok": True}
    assert route.call_count == 2
    assert len(sleeper.calls) == 1
    assert 2.0 <= sleeper.calls[0] <= 2.25


async def test_primary_rate_limit_uses_reset_header(
    client: GitHubClient, mock_api: respx.MockRouter, sleeper: Sleeper
) -> None:
    reset = str(int(time.time()) + 5)
    mock_api.get("/x").mock(
        side_effect=[
            httpx.Response(
                403,
                headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": reset},
                json={"message": "API rate limit exceeded"},
            ),
            httpx.Response(200, json={}),
        ]
    )
    await client.get("/x")
    assert 3.0 <= sleeper.calls[0] <= 8.0


async def test_rate_limit_longer_than_budget_fails_fast(
    client: GitHubClient, mock_api: respx.MockRouter, sleeper: Sleeper
) -> None:
    route = mock_api.get("/x").respond(429, headers={"Retry-After": "600"}, json={})
    with pytest.raises(RateLimitError) as ei:
        await client.get("/x")
    assert route.call_count == 1
    assert sleeper.calls == []  # never sleeps past the budget
    assert ei.value.retry_after == 600


async def test_5xx_retried_then_succeeds_with_backoff(
    client: GitHubClient, mock_api: respx.MockRouter, sleeper: Sleeper
) -> None:
    route = mock_api.get("/x").mock(
        side_effect=[httpx.Response(502), httpx.Response(503), httpx.Response(200, json={"a": 1})]
    )
    assert (await client.get("/x")).data == {"a": 1}
    assert route.call_count == 3
    assert sleeper.calls == [0.5, 1.0]  # exponential, jitter pinned to upper bound


async def test_retry_exhaustion_is_bounded(
    client: GitHubClient, mock_api: respx.MockRouter, sleeper: Sleeper
) -> None:
    route = mock_api.get("/x").respond(500)
    with pytest.raises(GitHubAPIError) as ei:
        await client.get("/x")
    assert route.call_count == 4  # 1 try + 3 retries
    assert len(sleeper.calls) == 3
    assert ei.value.status_code == 500
    assert "temporary" in ei.value.message


async def test_timeout_retried_then_network_error(
    client: GitHubClient, mock_api: respx.MockRouter
) -> None:
    route = mock_api.get("/x").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(NetworkError) as ei:
        await client.get("/x")
    assert route.call_count == 4
    assert "timed out" in ei.value.message


async def test_connection_error_recovers(client: GitHubClient, mock_api: respx.MockRouter) -> None:
    mock_api.get("/x").mock(
        side_effect=[httpx.ConnectError("boom"), httpx.Response(200, json={"ok": 1})]
    )
    assert (await client.get("/x")).data == {"ok": 1}


async def test_post_is_not_replayed_on_5xx(
    client: GitHubClient, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post("/x").respond(502)
    with pytest.raises(GitHubAPIError):
        await client.post("/x", {"a": 1})
    assert route.call_count == 1


async def test_post_is_replayed_on_rate_limit(
    client: GitHubClient, mock_api: respx.MockRouter
) -> None:
    route = mock_api.post("/x").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "1"}),
            httpx.Response(201, json={"id": 1}),
        ]
    )
    assert (await client.post("/x", {"a": 1})).data == {"id": 1}
    assert route.call_count == 2


async def test_non_json_body_is_unexpected_response(
    client: GitHubClient, mock_api: respx.MockRouter
) -> None:
    mock_api.get("/x").respond(200, content=b"<html>oops</html>")
    with pytest.raises(GitHubAPIError) as ei:
        await client.get("/x")
    assert "not valid JSON" in ei.value.message


async def test_oversized_response_rejected(make_settings, policy, mock_api) -> None:  # type: ignore[no-untyped-def]
    c = GitHubClient(make_settings(gitmcp_max_response_bytes=10_000), policy=policy)
    mock_api.get("/big").respond(200, content=b"x" * 20_000)
    with pytest.raises(GitHubAPIError) as ei:
        await c.get("/big")
    assert "limit" in ei.value.message
    await c.aclose()


async def test_unexpected_status(client: GitHubClient, mock_api: respx.MockRouter) -> None:
    mock_api.get("/x").respond(418)
    with pytest.raises(GitHubAPIError) as ei:
        await client.get("/x")
    assert ei.value.status_code == 418
    assert not ei.value.transient
