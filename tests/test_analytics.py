"""analytics server: semantic layer, RLS by API key, pagination, guarded SQL escape hatch."""

from __future__ import annotations

import pytest
from fastmcp import Client
from fastmcp.exceptions import MCPError

from forge import build_http_app
from forge.config import Settings, parse_api_keys
from servers.analytics.server import mcp
from tests.conftest import http_client

PERIOD = {"start": "2026-01-01", "end": "2026-12-31"}


async def test_kpis_and_breakdowns_are_consistent():
    async with Client(mcp) as c:
        k = (await c.call_tool("kpis", PERIOD)).data
        assert k["orders"] == 60 and 0 <= k["refund_rate"] <= 1 and k["scope_countries"] == "all"
        by_country = (await c.call_tool("revenue", {"group_by": "country", "status": "all", **PERIOD})).data
        by_cat = (await c.call_tool("revenue", {"group_by": "category", "status": "all", **PERIOD})).data
        assert by_country["total_revenue_eur"] == by_cat["total_revenue_eur"] > 0
        net = (await c.call_tool("revenue", {"group_by": "status", "status": "all", **PERIOD})).data
        assert round(sum(r["revenue_eur"] for r in net["rows"] if r["status"] != "refunded"), 2) == k["net_revenue_eur"]


async def test_pagination_and_customer_360():
    async with Client(mcp) as c:
        p1 = (await c.call_tool("top_customers", {"n": 3, **PERIOD})).data
        p2 = (await c.call_tool("top_customers", {"n": 3, "offset": p1["next_offset"], **PERIOD})).data
        assert len(p1["rows"]) == 3 and p1["next_offset"] == 3
        assert {r["customer_id"] for r in p1["rows"]}.isdisjoint({r["customer_id"] for r in p2["rows"]})
        c360 = (await c.call_tool("customer_360", {"customer_id": p1["rows"][0]["customer_id"]})).data
        assert c360["lifetime_value_eur"] >= p1["rows"][0]["net_revenue_eur"] and c360["orders"]
        with pytest.raises(MCPError, match="not found"):
            await c.call_tool("customer_360", {"customer_id": 9999})
        s = (await c.call_tool("search_products", {"category": "luggage", "limit": 1})).data
        assert s["rows"][0]["category"] == "luggage" and s["next_offset"] == 1


async def test_validation_and_sql_guard():
    async with Client(mcp) as c:
        with pytest.raises(MCPError, match="after end"):
            await c.call_tool("kpis", {"start": "2026-12-31", "end": "2026-01-01"})
        with pytest.raises(MCPError):  # Literal validation: unknown dimension never reaches SQL
            await c.call_tool("revenue", {"group_by": "customers; DROP TABLE orders"})
        r = (await c.call_tool("sql", {"query": "SELECT COUNT(*) AS n FROM products"})).data
        assert r["rows"][0]["n"] == 6
        with pytest.raises(MCPError, match="Only SELECT"):
            await c.call_tool("sql", {"query": "DELETE FROM orders"})


async def test_row_level_security_by_api_key():
    keys = "k-fr:agent-fr:analytics:read|country:FR;k-all:agent-hq:analytics:read|analytics:sql"
    s = Settings(transport="http", api_keys=parse_api_keys(keys))
    mcp.auth = __import__("forge.auth", fromlist=["ApiKeyVerifier"]).ApiKeyVerifier(s.api_keys)
    try:
        app = build_http_app(mcp, s)
        async with app.router.lifespan_context(app):
            async with http_client(app, {"Authorization": "Bearer k-fr"}) as c:
                k = (await c.call_tool("kpis", PERIOD)).data
                assert k["scope_countries"] == ["FR"]
                rows = (await c.call_tool("revenue", {"group_by": "country", "status": "all", **PERIOD})).data["rows"]
                assert [r["country"] for r in rows] == ["FR"]
                assert "sql" not in {t.name for t in await c.list_tools()}  # no analytics:sql scope
                with pytest.raises(MCPError, match="not found"):
                    await c.call_tool("customer_360", {"customer_id": 2})  # Bob Chen is in SG
            async with http_client(app, {"Authorization": "Bearer k-all"}) as c:
                assert "sql" in {t.name for t in await c.list_tools()}
                assert (await c.call_tool("kpis", PERIOD)).data["scope_countries"] == "all"
    finally:
        mcp.auth = None
