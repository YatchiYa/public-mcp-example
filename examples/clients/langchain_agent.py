"""LangChain / LangGraph via langchain-mcp-adapters.  pip install langchain-mcp-adapters langchain[openai]

    MCP_URL=... MCP_API_KEY=... OPENAI_API_KEY=... python langchain_agent.py
"""

import asyncio
import os

from langchain_mcp_adapters.client import MultiServerMCPClient

URL = os.environ.get("MCP_URL", "http://localhost:8000/mcp")
HEADERS = {"Authorization": f"Bearer {os.environ['MCP_API_KEY']}"} if os.environ.get("MCP_API_KEY") else {}


async def main() -> None:
    client = MultiServerMCPClient({"travel": {"transport": "http", "url": URL, "headers": HEADERS}})
    tools = await client.get_tools()
    print("tools:", [t.name for t in tools])

    # Tools work standalone (no LLM needed) ...
    fx = next(t for t in tools if t.name == "convert_currency")
    print(await fx.ainvoke({"amount": 10, "from_currency": "EUR", "to_currency": "USD"}))

    # ... or inside an agent:
    if os.environ.get("OPENAI_API_KEY"):
        from langchain.agents import create_agent

        agent = create_agent("openai:gpt-4.1-mini", tools)
        out = await agent.ainvoke({"messages": "What's the weather in Lisbon tomorrow?"})
        print(out["messages"][-1].content)


asyncio.run(main())
