FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY forge ./forge
COPY servers ./servers
RUN useradd --create-home --uid 1000 app && chown -R app:app /app
USER app
ARG SERVER=servers.travel
ENV SERVER=${SERVER} MCP_TRANSPORT=http MCP_HOST=0.0.0.0 MCP_PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys;sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/health').status!=200)"
CMD ["sh", "-c", "uv run --no-sync python -m forge run $SERVER"]
