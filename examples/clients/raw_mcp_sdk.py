"""Official MCP Python SDK (works with 1.x and 2.x), no framework. pip install mcp

    MCP_URL=http://localhost:8000/mcp MCP_API_KEY=dev-key-alice python raw_mcp_sdk.py
"""

import asyncio
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

URL = os.environ.get("MCP_URL", "http://localhost:8000/mcp")
HEADERS = {"Authorization": f"Bearer {os.environ['MCP_API_KEY']}"} if os.environ.get("MCP_API_KEY") else None


async def main() -> None:
    async with (
        streamablehttp_client(URL, headers=HEADERS) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        tools = await session.list_tools()
        print("tools:", [t.name for t in tools.tools])
        result = await session.call_tool("local_time", {"timezone": "Europe/Paris"})
        print(result.structuredContent)   # typed output
        print(result.content[0].text)     # text fallback


asyncio.run(main())
