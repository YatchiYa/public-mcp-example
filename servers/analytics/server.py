"""Analytics MCP: an *advanced* data-exposing server on top of a relational database.

Instead of (only) raw SQL, it offers a **semantic layer**: business questions map to curated,
parameterised tools whose SQL is built from whitelisted dimensions (no injection surface), with
  - row-level security derived from the API key (scopes `country:FR` restrict every tool),
  - pagination (limit/offset) and hard row caps,
  - a data dictionary resource with business definitions,
  - a guarded raw-SQL escape hatch for power users (scope analytics:sql),
  - a report prompt.
Schema (see servers/database/seed.py): customers(id,name,country,signup_date)
products(id,name,category,price_eur) orders(id,customer_id,product_id,quantity,order_date,status)
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from pydantic import Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from forge import create_server, run, scopes
from servers.database.server import DATABASE_URL as _URL
from servers.database.server import _assert_read_only, jsonable, make_engine

engine = make_engine(_URL)
MAX_ROWS = 500

DIMENSIONS = {  # whitelisted GROUP BY expressions: the LLM picks a key, never writes SQL here
    "month": "substr(o.order_date, 1, 7)",
    "country": "c.country",
    "category": "p.category",
    "product": "p.name",
    "status": "o.status",
}
Dimension = Literal["month", "country", "category", "product", "status"]
Status = Literal["paid", "shipped", "refunded", "all"]

DICTIONARY = {
    "revenue": "SUM(orders.quantity * products.price_eur) in EUR, at the catalogue price at query time",
    "order": "one row of `orders` = one product line; status in paid | shipped | refunded",
    "net revenue": "revenue excluding refunded orders (status != 'refunded')",
    "refund rate": "refunded orders / all orders, by count",
    "AOV": "average order value = net revenue / number of non-refunded orders",
    "new customers": "customers whose signup_date falls in the period",
    "tables": {
        "customers": "id, name, country (ISO-2), signup_date",
        "products": "id, name, category (outdoor|electronics|luggage|apparel), price_eur",
        "orders": "id, customer_id -> customers.id, product_id -> products.id, quantity, order_date, status",
    },
}

mcp = create_server(
    "analytics",
    instructions=(
        "Business analytics over the e-commerce database. Prefer the curated tools (kpis, revenue, "
        "top_customers, customer_360, search_products); read analytics://dictionary for definitions. "
        "Use `sql` only when no curated tool answers the question. Dates are ISO (YYYY-MM-DD). "
        "Some API keys are restricted to specific countries: results are silently scoped."
    ),
    readiness=lambda: engine.connect().execute(text("SELECT 1")),
)


# ---------- helpers -----------------------------------------------------------------------------
def _allowed_countries() -> list[str] | None:
    """Row-level security: scopes like `country:FR` on the caller's key restrict every query."""
    token = get_access_token()
    if token is None:
        return None
    countries = [s.split(":", 1)[1].upper() for s in token.scopes if s.startswith("country:")]
    return countries or None


def _period(start: dt.date | None, end: dt.date | None) -> tuple[dt.date, dt.date]:
    end = end or dt.datetime.now(dt.UTC).date()
    start = start or end.replace(day=1) - dt.timedelta(days=365)
    if start > end:
        raise ToolError(f"start ({start}) is after end ({end})")
    return start, end


def _where(start: dt.date, end: dt.date, status: Status, params: dict) -> str:
    clauses = ["o.order_date BETWEEN :start AND :end"]
    params.update(start=start.isoformat(), end=end.isoformat())
    if status != "all":
        clauses.append("o.status = :status")
        params["status"] = status
    countries = _allowed_countries()
    if countries:
        clauses.append("c.country IN :countries")
        params["countries"] = tuple(countries)
    return " AND ".join(clauses)


def _rows(sql: str, params: dict, limit: int | None = MAX_ROWS) -> list[dict]:
    try:
        with engine.connect() as conn:
            conn = conn.execution_options(postgresql_readonly=True)
            stmt = text(sql)
            if "countries" in params:
                from sqlalchemy import bindparam

                stmt = stmt.bindparams(bindparam("countries", expanding=True))
            result = conn.execute(stmt, params)
            rows = result.mappings().fetchmany(limit) if limit else result.mappings().all()
            return [{k: jsonable(v) for k, v in r.items()} for r in rows]
    except SQLAlchemyError as e:
        raise ToolError(f"SQL error: {str(getattr(e, 'orig', e)).splitlines()[0][:300]}")


_FROM = "FROM orders o JOIN customers c ON c.id = o.customer_id JOIN products p ON p.id = o.product_id"


# ---------- curated tools (semantic layer) ------------------------------------------------------
@mcp.tool(annotations={"readOnlyHint": True}, auth=scopes("analytics:read"))
def kpis(
    start: Annotated[dt.date | None, Field(description="Period start (inclusive), default: 12 months ago")] = None,
    end: Annotated[dt.date | None, Field(description="Period end (inclusive), default: today")] = None,
) -> dict:
    """Headline KPIs for a period: net revenue, orders, AOV, refund rate, new customers, active customers."""
    start, end = _period(start, end)
    params: dict = {}
    where = _where(start, end, "all", params)
    row = _rows(
        f"""SELECT
              COALESCE(SUM(CASE WHEN o.status != 'refunded' THEN o.quantity * p.price_eur END), 0) AS net_revenue_eur,
              COUNT(*) AS orders,
              SUM(CASE WHEN o.status = 'refunded' THEN 1 ELSE 0 END) AS refunded_orders,
              COUNT(DISTINCT o.customer_id) AS active_customers
            {_FROM} WHERE {where}""",
        params,
    )[0]
    kept = row["orders"] - row["refunded_orders"]
    countries = _allowed_countries()
    new_params: dict = {"start": start.isoformat(), "end": end.isoformat()}
    new_sql = "SELECT COUNT(*) AS n FROM customers c WHERE c.signup_date BETWEEN :start AND :end"
    if countries:
        new_sql += " AND c.country IN :countries"
        new_params["countries"] = tuple(countries)
    return {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "scope_countries": countries or "all",
        "net_revenue_eur": round(row["net_revenue_eur"], 2),
        "orders": row["orders"],
        "refund_rate": round(row["refunded_orders"] / row["orders"], 4) if row["orders"] else 0.0,
        "aov_eur": round(row["net_revenue_eur"] / kept, 2) if kept else 0.0,
        "active_customers": row["active_customers"],
        "new_customers": _rows(new_sql, new_params)[0]["n"],
    }


@mcp.tool(annotations={"readOnlyHint": True}, auth=scopes("analytics:read"))
def revenue(
    group_by: Annotated[Dimension, Field(description="Dimension to break revenue down by")],
    start: dt.date | None = None,
    end: dt.date | None = None,
    status: Annotated[Status, Field(description="Filter on order status; 'all' includes refunds")] = "paid",
    limit: Annotated[int, Field(ge=1, le=MAX_ROWS)] = 50,
) -> dict:
    """Revenue and order count broken down by month, country, category, product or status."""
    start, end = _period(start, end)
    params: dict = {}
    where = _where(start, end, status, params)
    dim = DIMENSIONS[group_by]
    rows = _rows(
        f"""SELECT {dim} AS {group_by}, ROUND(SUM(o.quantity * p.price_eur), 2) AS revenue_eur,
                   COUNT(*) AS orders, SUM(o.quantity) AS units
            {_FROM} WHERE {where} GROUP BY {dim} ORDER BY revenue_eur DESC LIMIT :limit""",
        {**params, "limit": limit},
    )
    return {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "status": status,
        "group_by": group_by,
        "rows": rows,
        "total_revenue_eur": round(sum(r["revenue_eur"] for r in rows), 2),
    }


@mcp.tool(annotations={"readOnlyHint": True}, auth=scopes("analytics:read"))
def top_customers(
    n: Annotated[int, Field(ge=1, le=100)] = 10,
    offset: Annotated[int, Field(ge=0, description="Pagination offset")] = 0,
    start: dt.date | None = None,
    end: dt.date | None = None,
) -> dict:
    """Customers ranked by net revenue in the period, paginated (n + offset)."""
    start, end = _period(start, end)
    params: dict = {"limit": n, "offset": offset}
    where = _where(start, end, "all", params)
    rows = _rows(
        f"""SELECT c.id AS customer_id, c.name, c.country,
                   ROUND(SUM(CASE WHEN o.status != 'refunded' THEN o.quantity * p.price_eur ELSE 0 END), 2) AS net_revenue_eur,
                   COUNT(*) AS orders, MAX(o.order_date) AS last_order
            {_FROM} WHERE {where} GROUP BY c.id, c.name, c.country
            ORDER BY net_revenue_eur DESC LIMIT :limit OFFSET :offset""",
        params,
    )
    return {"period": {"start": start.isoformat(), "end": end.isoformat()}, "offset": offset, "rows": rows,
            "next_offset": offset + n if len(rows) == n else None}


@mcp.tool(annotations={"readOnlyHint": True}, auth=scopes("analytics:read"))
def customer_360(customer_id: Annotated[int, Field(ge=1)]) -> dict:
    """Everything about one customer: profile, lifetime value, order history, favourite category."""
    countries = _allowed_countries()
    params: dict = {"id": customer_id}
    scope = ""
    if countries:
        scope = " AND c.country IN :countries"
        params["countries"] = tuple(countries)
    profile = _rows(f"SELECT c.id, c.name, c.country, c.signup_date FROM customers c WHERE c.id = :id{scope}", params)
    if not profile:
        raise ToolError(f"Customer {customer_id} not found (or outside your allowed countries)")
    orders = _rows(
        """SELECT o.id AS order_id, o.order_date, p.name AS product, p.category, o.quantity,
                  ROUND(o.quantity * p.price_eur, 2) AS amount_eur, o.status
           FROM orders o JOIN products p ON p.id = o.product_id
           WHERE o.customer_id = :id ORDER BY o.order_date DESC""",
        {"id": customer_id},
    )
    net = round(sum(o["amount_eur"] for o in orders if o["status"] != "refunded"), 2)
    by_cat: dict[str, float] = {}
    for o in orders:
        by_cat[o["category"]] = by_cat.get(o["category"], 0) + o["amount_eur"]
    return {
        **profile[0],
        "lifetime_value_eur": net,
        "orders_count": len(orders),
        "favourite_category": max(by_cat, key=by_cat.get) if by_cat else None,
        "orders": orders,
    }


@mcp.tool(annotations={"readOnlyHint": True}, auth=scopes("analytics:read"))
def search_products(
    q: Annotated[str | None, Field(description="Case-insensitive substring of the product name")] = None,
    category: str | None = None,
    max_price_eur: Annotated[float | None, Field(gt=0)] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
    offset: Annotated[int, Field(ge=0)] = 0,
) -> dict:
    """Catalogue search with optional filters; paginated. Returns units sold (all time) per product."""
    clauses, params = ["1=1"], {"limit": limit, "offset": offset}
    if q:
        clauses.append("LOWER(p.name) LIKE :q")
        params["q"] = f"%{q.lower()}%"
    if category:
        clauses.append("p.category = :category")
        params["category"] = category
    if max_price_eur is not None:
        clauses.append("p.price_eur <= :max_price")
        params["max_price"] = max_price_eur
    rows = _rows(
        f"""SELECT p.id, p.name, p.category, p.price_eur,
                   COALESCE(SUM(CASE WHEN o.status != 'refunded' THEN o.quantity END), 0) AS units_sold
            FROM products p LEFT JOIN orders o ON o.product_id = p.id
            WHERE {' AND '.join(clauses)} GROUP BY p.id, p.name, p.category, p.price_eur
            ORDER BY units_sold DESC LIMIT :limit OFFSET :offset""",
        params,
    )
    return {"rows": rows, "offset": offset, "next_offset": offset + limit if len(rows) == limit else None}


# ---------- escape hatch --------------------------------------------------------------------------
@mcp.tool(annotations={"readOnlyHint": True}, auth=scopes("analytics:sql"))
def sql(
    query: Annotated[str, Field(description="One SELECT; :name placeholders for parameters")],
    params: dict | None = None,
    limit: Annotated[int, Field(ge=1, le=MAX_ROWS)] = 100,
) -> dict:
    """Raw read-only SQL for questions the curated tools cannot answer (requires analytics:sql).
    Country-restricted keys cannot use this tool (row-level security cannot be enforced on free SQL)."""
    if _allowed_countries():
        raise ToolError("Raw SQL is not available for country-restricted keys; use the curated tools")
    rows = _rows(_assert_read_only(query), params or {}, limit + 1)
    return {"rows": rows[:limit], "row_count": min(len(rows), limit), "truncated": len(rows) > limit}


# ---------- context for the agent ---------------------------------------------------------------
@mcp.resource("analytics://dictionary", mime_type="application/json")
def dictionary() -> dict:
    """Business definitions (revenue, AOV, refund rate...) and table semantics."""
    return DICTIONARY


@mcp.prompt
def weekly_report(week_start: str) -> str:
    """Prompt template: a one-page weekly business report."""
    return (
        f"Write a one-page report for the week starting {week_start}: call kpis for that week and for "
        "the previous week, revenue(group_by='category') and revenue(group_by='country'), "
        "top_customers(n=5). Highlight week-over-week changes (%), name the best category and "
        "country, flag refund rate above 10%. Cite the tool outputs you used."
    )


if __name__ == "__main__":
    run(mcp)
