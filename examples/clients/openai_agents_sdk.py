"""OpenAI Agents SDK.  pip install openai-agents

    MCP_URL=... MCP_API_KEY=... OPENAI_API_KEY=... python openai_agents_sdk.py
"""

import asyncio
import os

from agents import Agent, Runner
from agents.mcp import MCPServerStreamableHttp

URL = os.environ.get("MCP_URL", "http://localhost:8000/mcp")
HEADERS = {"Authorization": f"Bearer {os.environ['MCP_API_KEY']}"} if os.environ.get("MCP_API_KEY") else {}


async def main() -> None:
    async with MCPServerStreamableHttp(
        name="travel",
        params={"url": URL, "headers": HEADERS, "timeout": 15},
        cache_tools_list=True,
        max_retry_attempts=3,
    ) as server:
        print("tools:", [t.name for t in await server.list_tools()])
        if os.environ.get("OPENAI_API_KEY"):
            agent = Agent(name="Travel", instructions="Use the MCP tools. Geocode first.", mcp_servers=[server])
            result = await Runner.run(agent, "Is it raining in Tokyo right now?")
            print(result.final_output)


asyncio.run(main())
