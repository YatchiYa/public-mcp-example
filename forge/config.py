"""Runtime settings, read once from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _bool(name: str, default: bool) -> bool:
    v = _env(name)
    return default if not v else v.lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class ApiKey:
    key: str
    client_id: str
    scopes: tuple[str, ...] = ()


def parse_api_keys(raw: str) -> list[ApiKey]:
    """'key:client:scope1|scope2;key2:client2' -> [ApiKey, ...]. Empty string -> []."""
    keys: list[ApiKey] = []
    for entry in filter(None, (e.strip() for e in raw.split(";"))):
        parts = entry.split(":", 2)
        if len(parts) < 2 or not parts[0] or not parts[1]:
            raise ValueError(f"MCP_API_KEYS: bad entry {entry!r} (expected key:client_id[:scopes])")
        scopes = tuple(s for s in parts[2].split("|") if s) if len(parts) == 3 else ()
        keys.append(ApiKey(parts[0], parts[1], scopes))
    return keys


@dataclass(frozen=True)
class Settings:
    transport: str = field(default_factory=lambda: _env("MCP_TRANSPORT", "stdio"))
    host: str = field(default_factory=lambda: _env("MCP_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("MCP_PORT", "8000")))
    path: str = field(default_factory=lambda: _env("MCP_PATH", "/mcp"))
    api_keys: list[ApiKey] = field(default_factory=lambda: parse_api_keys(_env("MCP_API_KEYS")))
    rate_limit_rps: float = field(default_factory=lambda: float(_env("MCP_RATE_LIMIT_RPS", "0")))
    mask_errors: bool = field(default_factory=lambda: _bool("MCP_MASK_ERRORS", True))
    log_level: str = field(default_factory=lambda: _env("MCP_LOG_LEVEL", "INFO").upper())

    @property
    def auth_enabled(self) -> bool:
        return self.transport == "http" and bool(self.api_keys)
