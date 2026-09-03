.PHONY: install test lint seed travel db inspect new docker
install: ; uv sync
test: ; uv run pytest -q
lint: ; uv run ruff check .
seed: ; uv run python -m servers.database.seed
travel: ; MCP_TRANSPORT=http uv run python -m forge run servers.travel
db: seed ; MCP_TRANSPORT=http MCP_API_KEYS="$${MCP_API_KEYS:-dev-key-alice:alice:db:read|db:write;dev-key-bob:bob:db:read}" uv run python -m forge run servers.database
inspect: ; uv run python -m forge inspect $(S)
new: ; uv run python -m forge new $(NAME)
docker: ; docker compose up --build
