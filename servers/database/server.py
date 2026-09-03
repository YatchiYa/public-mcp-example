"""Database MCP: expose any SQLAlchemy-compatible database to an agent, safely.

- schema discovery (list_tables / describe_table)
- read-only SQL (`query`): statement guard + DB-level read-only connection + row cap  -> scope db:read
- writes (`execute`) only for keys holding scope db:write                              -> scope db:write
Set DATABASE_URL (sqlite:///demo.db, postgresql+psycopg://..., mysql+pymysql://...).
"""

from __future__ import annotations

import base64
import datetime as dt
import decimal
import os
import re
import uuid
from typing import Annotated, Any

from fastmcp.exceptions import ToolError
from pydantic import Field
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError

from forge import create_server, run, scopes

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./demo.db")
MAX_ROWS = int(os.environ.get("DB_MAX_ROWS", "200"))
STATEMENT_TIMEOUT_MS = int(os.environ.get("DB_STATEMENT_TIMEOUT_MS", "15000"))


def engine_kwargs(url: str) -> dict[str, Any]:
    """Pre-ping, small pool, and a server-side statement timeout where the dialect supports it."""
    kw: dict[str, Any] = {"pool_pre_ping": True, "future": True}
    if url.startswith("postgresql"):
        kw["connect_args"] = {"options": f"-c statement_timeout={STATEMENT_TIMEOUT_MS}"}
        kw.update(pool_size=5, max_overflow=5, pool_recycle=1800)
    elif url.startswith("mysql"):
        kw.update(pool_size=5, max_overflow=5, pool_recycle=1800)
    return kw


def make_engine(url: str):
    return create_engine(url, **engine_kwargs(url))


# ponytail: one sync engine; tools run in FastMCP's threadpool (run_in_thread default).
engine = make_engine(DATABASE_URL)


def readiness() -> None:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))


mcp = create_server(
    "database",
    instructions=(
        "SQL access to the application database. Start with list_tables, then describe_table, "
        "then write a SELECT for `query`. Never guess column names. Results are capped at "
        f"{MAX_ROWS} rows: use WHERE / LIMIT / aggregates."
    ),
    readiness=readiness,
)

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|replace|grant|revoke|attach|detach|pragma|vacuum|copy|call|do|merge|lock)\b",
    re.IGNORECASE,
)
_IDENT = r"^[A-Za-z_][A-Za-z0-9_]*$"


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", sql).strip().rstrip(";").strip()


def _assert_read_only(sql: str) -> str:
    sql = _strip_comments(sql)
    if not sql:
        raise ToolError("Empty query")
    if ";" in sql:
        raise ToolError("Only one statement per query")
    if not re.match(r"^(select|with)\b", sql, re.IGNORECASE):
        raise ToolError("Only SELECT (or WITH ... SELECT) statements are allowed in `query`")
    if _FORBIDDEN.search(sql):
        raise ToolError("Statement contains a write/DDL keyword; use `execute` (db:write scope)")
    return sql


def jsonable(v: Any) -> Any:
    """DB driver types -> JSON-safe values (Decimal, dates, UUID, bytes, memoryview)."""
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, dt.timedelta):
        return v.total_seconds()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(v)).decode()
    return v


def _run(sql: str, params: dict | None, limit: int | None, *, readonly: bool) -> dict:
    try:
        with engine.connect() as conn:
            if readonly:
                # DB-level guarantee on top of the regex guard: Postgres honours the readonly flag
                # via psycopg; SQLite via query_only. MySQL relies on the DB role (see docs/DEPLOY.md).
                conn = conn.execution_options(postgresql_readonly=True)
                if engine.dialect.name == "sqlite":
                    conn.exec_driver_sql("PRAGMA query_only = ON")
            try:
                result = conn.execute(text(sql), params or {})
                if not result.returns_rows:
                    conn.commit()
                    return {"rowcount": result.rowcount}
                rows = result.mappings().fetchmany(limit + 1) if limit else result.mappings().all()
                truncated = bool(limit) and len(rows) > limit
                rows = rows[:limit] if limit else rows
                return {
                    "columns": list(result.keys()),
                    "rows": [{k: jsonable(v) for k, v in r.items()} for r in rows],
                    "row_count": len(rows),
                    "truncated": truncated,
                }
            finally:
                if readonly and engine.dialect.name == "sqlite":
                    conn.exec_driver_sql("PRAGMA query_only = OFF")  # pooled connection is reused
    except SQLAlchemyError as e:
        # Only the driver's first line: enough for the LLM to fix the SQL, no internals.
        raise ToolError(f"SQL error: {str(e.orig if getattr(e, 'orig', None) else e).splitlines()[0][:300]}")


@mcp.tool(annotations={"readOnlyHint": True}, auth=scopes("db:read"))
def list_tables() -> list[dict]:
    """All tables/views with column counts. Call first to discover the schema."""
    insp = inspect(engine)
    return [
        {"name": t, "type": "table", "columns": len(insp.get_columns(t))} for t in insp.get_table_names()
    ] + [{"name": v, "type": "view", "columns": len(insp.get_columns(v))} for v in insp.get_view_names()]


@mcp.tool(annotations={"readOnlyHint": True}, auth=scopes("db:read"))
def describe_table(table: Annotated[str, Field(pattern=_IDENT)]) -> dict:
    """Columns (name, type, nullable), primary key and foreign keys of a table."""
    insp = inspect(engine)
    if table not in insp.get_table_names() + insp.get_view_names():
        raise ToolError(f"Unknown table {table!r}; call list_tables")
    return {
        "table": table,
        "columns": [
            {"name": c["name"], "type": str(c["type"]), "nullable": c["nullable"]} for c in insp.get_columns(table)
        ],
        "primary_key": insp.get_pk_constraint(table).get("constrained_columns", []),
        "foreign_keys": [
            {"columns": fk["constrained_columns"], "references": f"{fk['referred_table']}({', '.join(fk['referred_columns'])})"}
            for fk in insp.get_foreign_keys(table)
        ],
    }


@mcp.tool(annotations={"readOnlyHint": True}, auth=scopes("db:read"))
def query(
    sql: Annotated[str, Field(description="A single SELECT. Use :name placeholders for parameters.")],
    params: Annotated[dict | None, Field(description="Bind parameters, e.g. {'min_age': 30}")] = None,
    limit: Annotated[int, Field(ge=1, le=MAX_ROWS)] = 50,
) -> dict:
    """Run a read-only SQL query. Returns columns, rows (capped by `limit`) and a truncated flag."""
    return _run(_assert_read_only(sql), params, limit, readonly=True)


@mcp.tool(annotations={"destructiveHint": True}, auth=scopes("db:write"))
def execute(
    sql: Annotated[str, Field(description="One INSERT/UPDATE/DELETE with :name placeholders")],
    params: dict | None = None,
) -> dict:
    """Run a single write statement (requires the db:write scope). Returns affected rowcount."""
    sql = _strip_comments(sql)
    if ";" in sql:
        raise ToolError("Only one statement per call")
    if not re.match(r"^(insert|update|delete)\b", sql, re.IGNORECASE):
        raise ToolError("execute accepts INSERT, UPDATE or DELETE only")
    return _run(sql, params, None, readonly=False)


@mcp.resource("schema://tables", mime_type="application/json")
def schema_resource() -> list[dict]:
    """Full schema snapshot as a resource, for clients that prefer reading context up front."""
    insp = inspect(engine)
    return [
        {"table": t, "columns": [f"{c['name']} {c['type']}" for c in insp.get_columns(t)]}
        for t in insp.get_table_names()
    ]


@mcp.prompt
def analyze(question: str) -> str:
    """Prompt template: answer a business question with SQL."""
    return (
        f"Answer this question using the database tools: {question}\n"
        "1. list_tables  2. describe_table for relevant tables  3. one or more `query` calls "
        "with explicit column lists and LIMIT.  4. Reply with the numbers and the SQL you used."
    )


if __name__ == "__main__":
    run(mcp)
