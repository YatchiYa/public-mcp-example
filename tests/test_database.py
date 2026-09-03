"""database server against the seeded temp SQLite (see conftest)."""

from __future__ import annotations

import pytest
from fastmcp import Client
from fastmcp.exceptions import MCPError, ToolError

from servers.database.server import _assert_read_only, mcp


def test_read_only_guard():
    assert _assert_read_only("SELECT 1; -- c").startswith("SELECT")
    assert _assert_read_only("with x as (select 1) select * from x")
    for bad in ["DELETE FROM orders", "select 1; drop table orders", "SELECT 1 /* */ ; UPDATE t SET a=1",
                "PRAGMA table_info(orders)", "select * from orders union select * from x; delete from orders"]:
        with pytest.raises(ToolError):
            _assert_read_only(bad)


async def test_schema_discovery():
    async with Client(mcp) as c:
        tables = {t["name"] for t in (await c.call_tool("list_tables", {})).data}
        assert {"customers", "products", "orders"} <= tables
        d = (await c.call_tool("describe_table", {"table": "orders"})).data
        assert d["primary_key"] == ["id"]
        assert any(fk["references"].startswith("customers") for fk in d["foreign_keys"])
        with pytest.raises(MCPError, match="Unknown table"):
            await c.call_tool("describe_table", {"table": "nope"})


async def test_query_with_params_and_cap():
    async with Client(mcp) as c:
        r = (await c.call_tool("query", {"sql": "SELECT name FROM customers WHERE country = :c", "params": {"c": "FR"}})).data
        assert r["rows"] == [{"name": "Alice Martin"}] and r["truncated"] is False
        r = (await c.call_tool("query", {"sql": "SELECT id FROM orders", "limit": 10})).data
        assert r["row_count"] == 10 and r["truncated"] is True
        with pytest.raises(MCPError, match="SQL error"):
            await c.call_tool("query", {"sql": "SELECT nope FROM customers"})
        with pytest.raises(MCPError, match="Only SELECT"):
            await c.call_tool("query", {"sql": "DELETE FROM orders"})


async def test_execute_then_query():
    async with Client(mcp) as c:
        r = (await c.call_tool("execute", {"sql": "UPDATE orders SET status = 'paid' WHERE status = 'refunded'"})).data
        assert r["rowcount"] >= 1
        r = (await c.call_tool("query", {"sql": "SELECT COUNT(*) AS n FROM orders WHERE status='refunded'"})).data
        assert r["rows"][0]["n"] == 0
        with pytest.raises(MCPError):
            await c.call_tool("execute", {"sql": "DROP TABLE orders"})


async def test_resource_and_prompt():
    async with Client(mcp) as c:
        res = await c.read_resource("schema://tables")
        assert "orders" in res[0].text
        p = await c.get_prompt("analyze", {"question": "top products"})
        assert "list_tables" in p.messages[0].content.text


def test_jsonable_and_engine_options():
    import datetime as dt
    import decimal
    import uuid

    from servers.database.server import engine_kwargs, jsonable

    assert jsonable(decimal.Decimal("1.50")) == 1.5
    assert jsonable(dt.date(2026, 1, 2)) == "2026-01-02"
    assert jsonable(b"\x00\x01") == "AAE="
    u = uuid.uuid4()
    assert jsonable(u) == str(u)
    assert jsonable("x") == "x"
    assert "statement_timeout" in engine_kwargs("postgresql+psycopg://u:p@h/db")["connect_args"]["options"]
    assert "connect_args" not in engine_kwargs("sqlite:///x.db")


async def test_health_reports_db_readiness():
    import httpx

    from forge import build_http_app
    from forge.config import Settings

    app = build_http_app(mcp, Settings(transport="http"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
