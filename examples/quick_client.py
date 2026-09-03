"""Call any forge server from Python with the FastMCP client (same venv as the server).

    uv run python examples/quick_client.py http://localhost:8000/mcp dev-key-alice
"""

from __future__ import annotations

import asyncio
import sys

from fastmcp import Client


async def main(url: str, key: str | None) -> None:
    async with Client(url, auth=key) as c:
        for t in await c.list_tools():
            print(f"- {t.name}")
        if any(t.name == "geocode_place" for t in await c.list_tools()):
            print((await c.call_tool("geocode_place", {"query": "Tokyo", "limit": 1})).data)


asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000/mcp",
                 sys.argv[2] if len(sys.argv) > 2 else None))
