"""Gemini API, remote MCP through the Interactions API (Google calls your server; PUBLIC https required).
Server names must be snake_case (no '-').   pip install google-genai

    GOOGLE_API_KEY=... MCP_URL=https://your-host/mcp MCP_API_KEY=... python gemini_interactions_api.py
"""

import os

from google import genai

client = genai.Client()
interaction = client.interactions.create(
    model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
    input="Convert 250 EUR to JPY and tell me the weather in Osaka.",
    tools=[{
        "type": "mcp_server",
        "name": "travel_intel",
        "url": os.environ["MCP_URL"],
        "headers": {"Authorization": f"Bearer {os.environ.get('MCP_API_KEY', '')}"},
        # "allowed_tools": ["convert_currency", "geocode_place", "get_weather"],
    }],
)
print(interaction)
