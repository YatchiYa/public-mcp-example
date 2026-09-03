"""One structured audit log line per tool call: who, what, how long, ok/error."""

from __future__ import annotations

import json
import logging
import time

from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext

log = logging.getLogger("forge.audit")


class AuditMiddleware(Middleware):
    async def on_call_tool(self, context: MiddlewareContext, call_next: CallNext):
        token = get_access_token()
        record = {
            "tool": getattr(context.message, "name", "?"),
            "client": token.client_id if token else "anonymous",
        }
        start = time.perf_counter()
        try:
            result = await call_next(context)
            record["ok"] = True
            return result
        except Exception as exc:
            record["ok"] = False
            record["error"] = f"{type(exc).__name__}: {exc}"[:300]
            raise
        finally:
            record["ms"] = round((time.perf_counter() - start) * 1000, 1)
            log.info(json.dumps(record))
