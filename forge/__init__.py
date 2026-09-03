"""mcp-forge: build a robust MCP server in one file.

    from forge import create_server, run

    mcp = create_server("my-server", instructions="What this server is for.")

    @mcp.tool
    def hello(name: str) -> str:
        return f"Hello {name}"

    if __name__ == "__main__":
        run(mcp)

`run()` picks transport, auth, middleware and hardening from environment variables
(see .env.example) so the same file serves Claude Desktop (stdio) and Google ADK (HTTP).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import anyio
import uvicorn
from fastmcp import FastMCP
from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
from fastmcp.server.middleware.rate_limiting import RateLimitingMiddleware
from starlette.responses import JSONResponse

from forge.auth import ApiKeyHeaderMiddleware, ApiKeyVerifier, scopes
from forge.config import Settings
from forge.middleware import AuditMiddleware

__all__ = ["Settings", "build_http_app", "create_server", "run", "scopes"]


def create_server(
    name: str,
    instructions: str | None = None,
    *,
    version: str = "0.1.0",
    settings: Settings | None = None,
    readiness: Callable[[], None] | None = None,
) -> FastMCP:
    """A FastMCP server pre-wired with auth (if keys configured), error handling and audit logs.

    `readiness` (optional) is called by GET /health; raise to report 503 (e.g. DB unreachable).
    """
    s = settings or Settings()
    logging.basicConfig(level=s.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    middleware = [ErrorHandlingMiddleware(), AuditMiddleware()]
    if s.rate_limit_rps > 0:
        middleware.append(RateLimitingMiddleware(max_requests_per_second=s.rate_limit_rps))

    mcp = FastMCP(
        name,
        instructions=instructions,
        version=version,
        auth=ApiKeyVerifier(s.api_keys) if s.auth_enabled else None,
        middleware=middleware,
        mask_error_details=s.mask_errors,
    )
    mcp.forge_readiness = readiness  # type: ignore[attr-defined]
    return mcp


def build_http_app(mcp: FastMCP, settings: Settings | None = None):
    """Starlette ASGI app: MCP at settings.path, plus GET /health. Use with uvicorn or tests."""
    s = settings or Settings()
    app = mcp.http_app(path=s.path)
    app.add_middleware(ApiKeyHeaderMiddleware)  # outermost: runs before FastMCP's bearer auth

    async def health(_):
        body = {"status": "ok", "server": mcp.name, "version": mcp.version, "auth": s.auth_enabled}
        check = getattr(mcp, "forge_readiness", None)
        if check is not None:
            try:
                await anyio.to_thread.run_sync(check)
            except Exception as exc:  # noqa: BLE001 - any failure means "not ready"
                logging.getLogger("forge").warning("readiness failed: %s", exc)
                return JSONResponse({**body, "status": "degraded", "error": type(exc).__name__}, status_code=503)
        return JSONResponse(body)

    app.add_route("/health", health, methods=["GET"])
    return app


def run(mcp: FastMCP, settings: Settings | None = None) -> None:
    s = settings or Settings()
    if s.transport == "stdio":
        mcp.run(transport="stdio", show_banner=False)
        return
    if s.transport != "http":
        raise SystemExit(f"MCP_TRANSPORT must be 'stdio' or 'http', got {s.transport!r}")
    if not s.api_keys:
        logging.getLogger("forge").warning("HTTP transport with NO auth (MCP_API_KEYS empty)")
    logging.getLogger("forge").info("MCP '%s' on http://%s:%s%s", mcp.name, s.host, s.port, s.path)
    # ponytail: one worker per container (in-memory rate limiter); scale with replicas behind the proxy.
    uvicorn.run(
        build_http_app(mcp, s),
        host=s.host,
        port=s.port,
        log_level=s.log_level.lower(),
        proxy_headers=True,          # trust X-Forwarded-* from Caddy/nginx for correct client IPs in logs
        forwarded_allow_ips="*",
        timeout_graceful_shutdown=10,
    )
