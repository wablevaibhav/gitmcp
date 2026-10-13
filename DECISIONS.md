# Design decisions

Context: GitMCP v0.1.0, Python 3.14 on a Windows 11 laptop, official MCP Python SDK 2.3.0, GitHub REST API.
Numbers are from the test suite and live runs described in [README.md](README.md#results-at-a-glance).

---

## 1. Keep GitHub logic independent of the MCP layer

**Decision:** `github/*.py` services take typed Pydantic inputs and return typed outputs and know nothing about
MCP. `server.py` is a thin adapter that registers tools and translates errors.

**Why:** the logic can be tested with plain `httpx` mocks (no server, no protocol), and the MCP SDK, which
changed substantially between 1.x and 2.x, is isolated to one file.

| Option | Test style | MCP SDK change blast radius |
| ------ | ---------- | --------------------------- |
| Services + thin adapter (chosen) | Direct function calls | 1 file (`server.py`) |
| Logic inside tool functions | Needs an MCP client in every test | Every tool |

**Trade-off:** one extra layer, and each tool is declared twice (service method plus a registered wrapper).

---

## 2. Treat retry safety differently for reads and writes

**Decision:** GET requests are retried on 429, 500/502/503/504, timeouts and connection errors. POST requests
are retried **only** on rate limits (where GitHub rejected the request outright), never after a timeout or
5xx.

**Why:** a timed-out `POST /issues` may have succeeded. Replaying it would create a duplicate issue, comment
or pull request. The retry budget is also bounded: 3 retries by default, exponential backoff with full
jitter, and a rate-limit wait is never slept past `GITHUB_MAX_RATE_LIMIT_WAIT`; it fails with the reset time
instead of blocking an assistant for minutes. This is covered by dedicated tests.

**Trade-off:** a write that fails on a transient 5xx surfaces an error to the user instead of self-healing.
For a tool that mutates a repository I judged that safer than a possible duplicate.

---

## 3. Writes are opt-in and enforced twice

**Decision:** write tools are not registered at all unless `GITHUB_ENABLE_WRITE_TOOLS=true`, and the service
methods independently refuse when the flag is off.

**Why:** an assistant cannot call or even discover a tool that does not exist, which is stronger than a tool
that exists and returns "denied". The second check is defence in depth against a future registration bug.
GitHub's own token permissions remain the final authority.

**Trade-off:** changing the setting requires restarting the client, and the flag is global rather than
per-repository.

---

## 4. Strict schemas and a single `params` object per tool

**Decision:** every tool takes one `params` model with `extra="forbid"`, validated owner/repo/ref/path
formats, and bounded page sizes.

**Why:** unknown or malformed arguments are rejected before any HTTP call, which is verified by a test that
asserts zero requests were made. Path traversal (`..`), invalid Git ref characters and oversized queries
cannot reach the API. It also yields precise JSON Schema (enums, min/max, descriptions) for the model.

**Trade-off:** the nested `{"params": {...}}` shape is slightly less natural for a model than flat arguments.

---

## 5. Fail loudly on content and responses instead of silently truncating

**Decision:** files over `GITMCP_MAX_FILE_BYTES` (200 KB), binary files, and directories return a clear typed
error. Diffs and comment bodies are truncated but flagged (`patch_truncated`, `body_truncated`). Any single
GitHub response over 5 MB is aborted while streaming.

**Why:** a silently clipped source file can lead an assistant to a confidently wrong answer, whereas an error
tells it to look elsewhere. Diffs and comments are different: a prefix is still useful, and the flag makes
the cut explicit. Streaming with a hard cap bounds memory.

**Trade-off:** large files cannot be read at all in one call, and a model must page or choose another file.

---

## 6. Redact secrets at the logging and error boundary

**Decision:** the token is a `SecretStr`, is never placed in error text, and is stripped from log records and
exception text by a logging filter that matches the exact value and common token shapes. Unexpected
exceptions return a generic message and log only the exception type.

**Why:** library exception messages can echo request details. Rather than trust every call site, the
boundary scrubs output; tests assert that a token embedded in an upstream error and in a raised exception
never reaches the client.

**Trade-off:** unexpected failures are harder to diagnose from the client side; details are only in server
logs at `DEBUG`/`ERROR` and intentionally omit messages.
