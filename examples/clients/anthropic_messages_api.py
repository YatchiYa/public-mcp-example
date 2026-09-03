"""Claude Messages API with the built-in MCP connector (no MCP client code at all).
Requirement: the server must be reachable over PUBLIC https (deploy it, or `cloudflared tunnel --url http://localhost:8000`).

    pip install anthropic ; ANTHROPIC_API_KEY=... MCP_URL=https://your-host/mcp MCP_API_KEY=... python anthropic_messages_api.py
"""

import os

import anthropic

client = anthropic.Anthropic()
response = client.beta.messages.create(
    model="claude-opus-5",
    max_tokens=1000,
    messages=[{"role": "user", "content": "What's the local time and weather in Kyoto?"}],
    mcp_servers=[{
        "type": "url",
        "url": os.environ["MCP_URL"],                       # https://... required
        "name": "travel",
        "authorization_token": os.environ.get("MCP_API_KEY"),  # sent as Authorization: Bearer
    }],
    tools=[{"type": "mcp_toolset", "mcp_server_name": "travel"}],
    betas=["mcp-client-2025-11-20"],
)
for block in response.content:
    if block.type == "text":
        print(block.text)
    elif block.type == "mcp_tool_use":
        print("-> called", block.name, block.input)
