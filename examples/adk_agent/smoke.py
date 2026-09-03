"""No-LLM smoke test: prove ADK's McpToolset can list and call tools on the running server.

    MCP_URL=http://localhost:8000/mcp MCP_API_KEY=dev-key-alice python smoke.py
"""

from __future__ import annotations

import asyncio
import os

from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset


async def main() -> None:
    key = os.environ.get("MCP_API_KEY", "")
    ts = McpToolset(connection_params=StreamableHTTPConnectionParams(
        url=os.environ.get("MCP_URL", "http://localhost:8000/mcp"),
        headers={"Authorization": f"Bearer {key}"} if key else None))
    tools = {t.name: t for t in await ts.get_tools()}
    print("tools:", sorted(tools))
    if "geocode_place" in tools:
        print(await tools["geocode_place"].run_async(args={"query": "Lisbon", "limit": 1}, tool_context=None))
    if "list_tables" in tools:
        print(await tools["list_tables"].run_async(args={}, tool_context=None))
    await ts.close()


asyncio.run(main())
