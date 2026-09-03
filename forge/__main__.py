"""CLI:  python -m forge run servers.travel      (serve)
        python -m forge inspect servers.travel  (list tools/resources/prompts)
        python -m forge new my_server            (scaffold servers/my_server from the template)
"""

from __future__ import annotations

import asyncio
import importlib
import shutil
import sys
from pathlib import Path

from fastmcp import FastMCP


def _load(module: str) -> FastMCP:
    mod = importlib.import_module(module if module.endswith(".server") else f"{module}.server")
    return mod.mcp


def cmd_run(module: str) -> None:
    from forge import run

    run(_load(module))


def cmd_inspect(module: str) -> None:
    from fastmcp import Client

    async def go():
        async with Client(_load(module)) as c:
            print("Tools:")
            for t in await c.list_tools():
                print(f"  - {t.name}: {(t.description or '').strip().splitlines()[0]}")
            print("Resources:")
            for r in await c.list_resources():
                print(f"  - {r.uri}")
            print("Prompts:")
            for p in await c.list_prompts():
                print(f"  - {p.name}")

    asyncio.run(go())


def cmd_new(name: str) -> None:
    src = Path(__file__).parent.parent / "servers" / "_template"
    dst = src.parent / name
    if dst.exists():
        raise SystemExit(f"{dst} already exists")
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
    server = dst / "server.py"
    server.write_text(server.read_text().replace("__NAME__", name.replace("_", "-")))
    print(f"Created {dst}/ -> edit server.py, then: python -m forge run servers.{name}")


def main(argv: list[str]) -> None:
    cmds = {"run": cmd_run, "inspect": cmd_inspect, "new": cmd_new}
    if len(argv) != 2 or argv[0] not in cmds:
        raise SystemExit(__doc__)
    cmds[argv[0]](argv[1])


if __name__ == "__main__":
    main(sys.argv[1:])
