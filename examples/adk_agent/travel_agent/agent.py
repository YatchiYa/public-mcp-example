"""Google ADK agent that uses the travel-intel (and optionally database) MCP servers over HTTP.

    cd examples/adk_agent
    uv venv && source .venv/bin/activate && uv pip install -r requirements.txt
    export GOOGLE_API_KEY=...            # Gemini key (or Vertex AI settings)
    export MCP_URL=http://localhost:8000/mcp
    export MCP_API_KEY=dev-key-alice     # only if the server has MCP_API_KEYS set
    adk web                              # or: adk run travel_agent
"""

from __future__ import annotations

import os

from google.adk.agents.llm_agent import LlmAgent
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

MCP_URL = os.environ.get("MCP_URL", "http://localhost:8000/mcp")
MCP_API_KEY = os.environ.get("MCP_API_KEY", "")

headers = {"Authorization": f"Bearer {MCP_API_KEY}"} if MCP_API_KEY else None

root_agent = LlmAgent(
    model=os.environ.get("ADK_MODEL", "gemini-2.5-flash"),
    name="travel_assistant",
    instruction=(
        "You are a pragmatic travel assistant. Always geocode a place before asking for weather, "
        "air quality or local time. Give temperatures in °C and convert budgets when asked."
    ),
    tools=[
        McpToolset(
            connection_params=StreamableHTTPConnectionParams(url=MCP_URL, headers=headers, timeout=15),
            # tool_filter=["geocode_place", "get_weather"],   # optional allow-list
        )
    ],
)
