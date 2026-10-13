"""Connect to GitMCP over stdio like a real MCP client, list tools and call one.

Usage:
    python scripts/verify_tools.py                      # list tools only
    python scripts/verify_tools.py octocat/Hello-World  # also call github_get_repository

Uses GITHUB_TOKEN from the environment if set; public repos work without one.
"""

from __future__ import annotations

import asyncio
import os
import sys

from mcp import Client, StdioServerParameters


async def main(repo: str | None) -> int:
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "gitmcp"], env={**os.environ}
    )
    async with Client(params) as client:
        tools = (await client.list_tools()).tools
        print(f"Connected. {len(tools)} tools:")
        for t in tools:
            print(f"  - {t.name}")
        if repo:
            owner, _, name = repo.partition("/")
            result = await client.call_tool(
                "github_get_repository", {"params": {"owner": owner, "repo": name}}
            )
            print("is_error:", result.is_error)
            print(result.structured_content or result.content)
            return 1 if result.is_error else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None)))
