# mcp-forge — design (2026-09-02)

## Goal
A small kit to build **robust MCP servers** callable from Google ADK, Claude, or any MCP client,
with or without API-key auth, that a developer can copy in one file to expose a new capability
(e.g. a database).

## Decisions
| Topic | Choice | Why |
|---|---|---|
| Framework | FastMCP 4.0.x (MCP SDK 2.x, spec 2026-07-28) | Middleware, auth providers, in-memory test client, per-connection protocol negotiation with older clients |
| Transports | stdio (Claude Desktop) + Streamable HTTP (ADK, remote) | Chosen by `MCP_TRANSPORT`; same server file |
| Auth | `TokenVerifier` over static API keys (`MCP_API_KEYS`), `Authorization: Bearer` or `X-API-Key` | Simple now; swap `ApiKeyVerifier` for JWT/OAuth later without touching servers |
| Scopes | `forge.scopes("db:write")` per tool | Enforced when auth is on, transparent when off (stdio / dev) |
| Hardening | error-handling + audit middleware, optional rate limit, masked internals, `/health` | Production defaults with zero code in servers |
| Tests | In-memory `Client` + in-process ASGI HTTP for auth paths | No sockets, no network, < 1 s |
| ADK client | Separate venv (`google-adk[mcp]` pins MCP SDK 1.x) | Verified interop: SDK 1.x client ↔ FastMCP 4 server |

## Components
- `forge/config.py` – env → `Settings`, `parse_api_keys`.
- `forge/auth.py` – `ApiKeyVerifier`, `ApiKeyHeaderMiddleware`, `scopes()`.
- `forge/middleware.py` – `AuditMiddleware` (one JSON line per tool call).
- `forge/__init__.py` – `create_server()`, `build_http_app()`, `run()`.
- `forge/__main__.py` – `run | inspect | new` CLI.
- `servers/travel` – example, key-less public APIs (Open-Meteo, Frankfurter, zoneinfo).
- `servers/database` – example, SQLAlchemy, read-only guard, write scope.
- `servers/_template` – scaffold source for `python -m forge new`.

## Error handling
Business errors raise `ToolError` (message reaches the LLM). Unexpected exceptions are logged
with traceback server-side and masked client-side (`MCP_MASK_ERRORS`). Upstream HTTP errors and
SQL errors are translated to short actionable `ToolError`s.

## Out of scope (for now)
OAuth 2.1 / DCR flows, key storage in a DB, per-tenant DB routing, MCP Tasks extension, MCP Apps.
