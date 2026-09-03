"""forge core: config parsing, API-key auth over HTTP, X-API-Key mapping, scopes, /health."""

from __future__ import annotations

import httpx
import pytest
from fastmcp.exceptions import MCPError

from forge import build_http_app, create_server, scopes
from forge.config import ApiKey, Settings, parse_api_keys
from tests.conftest import http_client

KEYS = "k-alice:alice:db:read|db:write;k-bob:bob:db:read"


def test_parse_api_keys():
    assert parse_api_keys("") == []
    assert parse_api_keys(KEYS) == [
        ApiKey("k-alice", "alice", ("db:read", "db:write")),
        ApiKey("k-bob", "bob", ("db:read",)),
    ]
    with pytest.raises(ValueError):
        parse_api_keys("nocolon")


def _secured():
    s = Settings(transport="http", api_keys=parse_api_keys(KEYS))
    mcp = create_server("secured", settings=s)

    @mcp.tool
    def ping() -> str:
        return "pong"

    @mcp.tool(auth=scopes("db:write"))
    def write() -> str:
        return "written"

    return build_http_app(mcp, s)


async def test_health_route():
    app = _secured()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/health")
    assert r.status_code == 200 and r.json()["auth"] is True


async def test_no_key_rejected():
    app = _secured()
    async with app.router.lifespan_context(app):
        with pytest.raises(MCPError):
            async with http_client(app) as c:
                await c.call_tool("ping", {})


async def test_bearer_and_x_api_key_accepted():
    app = _secured()
    async with app.router.lifespan_context(app):
        async with http_client(app, {"Authorization": "Bearer k-bob"}) as c:
            assert (await c.call_tool("ping", {})).data == "pong"
        async with http_client(app, {"X-API-Key": "k-bob"}) as c:
            assert (await c.call_tool("ping", {})).data == "pong"


async def test_scopes_enforced():
    app = _secured()
    async with app.router.lifespan_context(app):
        async with http_client(app, {"Authorization": "Bearer k-alice"}) as c:
            assert (await c.call_tool("write", {})).data == "written"
        async with http_client(app, {"Authorization": "Bearer k-bob"}) as c:
            names = {t.name for t in await c.list_tools()}
            assert "write" not in names  # hidden from clients lacking the scope
            with pytest.raises(MCPError):
                await c.call_tool("write", {})


async def test_no_auth_when_keys_empty():
    s = Settings(transport="http", api_keys=[])
    mcp = create_server("open", settings=s)

    @mcp.tool
    def ping() -> str:
        return "pong"

    app = build_http_app(mcp, s)
    async with app.router.lifespan_context(app), http_client(app) as c:
        assert (await c.call_tool("ping", {})).data == "pong"


async def test_scoped_tool_open_when_auth_disabled():
    from fastmcp import Client

    mcp = create_server("open", settings=Settings(transport="stdio"))

    @mcp.tool(auth=scopes("db:write"))
    def write() -> str:
        return "written"

    async with Client(mcp) as c:
        assert (await c.call_tool("write", {})).data == "written"
