"""Shared fixtures. DATABASE_URL is pointed at a temp SQLite file BEFORE servers.database is imported."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

_TMP = Path(tempfile.mkdtemp()) / "test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}"
os.environ["MCP_TRANSPORT"] = "stdio"  # in-memory tests: no HTTP auth unless a test opts in

from servers.database import seed

seed.main()


def http_client(app, headers: dict[str, str] | None = None) -> Client:
    """FastMCP Client speaking real Streamable HTTP to an ASGI app in-process (no sockets)."""

    def factory(headers=None, timeout=None, auth=None, **_):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers, timeout=timeout, auth=auth
        )

    return Client(StreamableHttpTransport("http://test/mcp", headers=headers, httpx_client_factory=factory))


@pytest.fixture
def anyio_backend():
    return "asyncio"
