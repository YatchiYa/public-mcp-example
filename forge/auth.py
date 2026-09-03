"""API-key authentication for HTTP transport.

Clients send `Authorization: Bearer <key>` (MCP standard) or `X-API-Key: <key>`
(rewritten to Bearer by `ApiKeyHeaderMiddleware`). Keys map to a client_id and
scopes; enforce scopes per tool with `@mcp.tool(auth=require_scopes("db:write"))`.
"""

from __future__ import annotations

import hmac

from fastmcp.server.auth import AccessToken, AuthCheck, AuthContext, TokenVerifier, require_scopes

from forge.config import ApiKey


def scopes(*names: str) -> AuthCheck:
    """`@mcp.tool(auth=scopes("db:write"))`: require scopes when auth is on, allow when it is off.

    FastMCP's plain `require_scopes` hides the tool whenever there is no token, which would
    empty the server in stdio / open-HTTP mode. With auth enabled, every request that reaches a
    tool already carries a verified token (401 otherwise), so `token is None` == "auth disabled".
    """
    check = require_scopes(*names)

    def _check(ctx: AuthContext) -> bool:
        return ctx.token is None or check(ctx)

    _check.__name__ = f"scopes{names}"
    return _check


class ApiKeyVerifier(TokenVerifier):
    """Verifies bearer tokens against a static key list (constant-time compare)."""

    def __init__(self, keys: list[ApiKey]):
        super().__init__()
        self._keys = {k.key: k for k in keys}

    async def verify_token(self, token: str) -> AccessToken | None:
        # ponytail: dict lookup + hmac.compare_digest; swap for a DB/secret-store lookup
        # (hash the keys at rest) when keys must rotate without a restart.
        for key, entry in self._keys.items():
            if hmac.compare_digest(key.encode(), token.encode()):
                return AccessToken(
                    token=token,
                    client_id=entry.client_id,
                    scopes=list(entry.scopes),
                    claims={"client_id": entry.client_id},
                )
        return None


class ApiKeyHeaderMiddleware:
    """Pure-ASGI middleware: `X-API-Key: k` -> `Authorization: Bearer k` (if no Authorization)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            if b"authorization" not in headers and b"x-api-key" in headers:
                scope = dict(scope)
                scope["headers"] = [
                    (k, v) for k, v in scope["headers"] if k != b"x-api-key"
                ] + [(b"authorization", b"Bearer " + headers[b"x-api-key"])]
        await self.app(scope, receive, send)
