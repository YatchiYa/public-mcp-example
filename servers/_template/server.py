"""__NAME__ MCP server. Copy-edit this file; everything else (transport, auth, logs) is inherited."""

from __future__ import annotations

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token

from forge import create_server, run, scopes

mcp = create_server(
    "__NAME__",
    instructions="Describe, for the LLM, what this server is for and when to use its tools.",
)


@mcp.tool(annotations={"readOnlyHint": True})
def echo(text: str) -> str:
    """Return the text unchanged. (Docstrings become the tool description the LLM reads.)"""
    if not text:
        raise ToolError("text must not be empty")  # ToolError messages reach the LLM verbatim
    return text


@mcp.tool(auth=scopes("admin"))
def whoami() -> dict:
    """Who is calling (requires the 'admin' scope when auth is enabled)."""
    token = get_access_token()
    return {"client_id": token.client_id if token else "anonymous"}


@mcp.resource("info://about")
def about() -> str:
    """Static context the client can read without calling a tool."""
    return "__NAME__ v0.1.0"


if __name__ == "__main__":
    run(mcp)
