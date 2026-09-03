#!/usr/bin/env bash
# Start (or restart) the two demo MCP servers in the background.   ./up.sh    |   ./up.sh down
set -e
cd "$(dirname "$0")"
export MCP_TRANSPORT=http MCP_HOST=0.0.0.0 MCP_RATE_LIMIT_RPS=20
export MCP_API_KEYS="${MCP_API_KEYS:-dev-key-alice:alice:db:read|db:write|analytics:read|analytics:sql;dev-key-bob:bob:db:read|analytics:read;dev-key-fr:agent-fr:analytics:read|country:FR}"
TRAVEL_PORT=${TRAVEL_PORT:-8002}; DB_PORT=${DB_PORT:-8001}; ANALYTICS_PORT=${ANALYTICS_PORT:-8003}

fuser -k "$TRAVEL_PORT/tcp" "$DB_PORT/tcp" "$ANALYTICS_PORT/tcp" 2>/dev/null || true
[ "$1" = "down" ] && { echo "stopped"; exit 0; }

mkdir -p logs
[ -f demo.db ] || uv run python -m servers.database.seed
MCP_PORT=$TRAVEL_PORT nohup uv run python -m forge run servers.travel   > logs/travel.log   2>&1 &
MCP_PORT=$DB_PORT     nohup uv run python -m forge run servers.database > logs/database.log 2>&1 &
MCP_PORT=$ANALYTICS_PORT nohup uv run python -m forge run servers.analytics > logs/analytics.log 2>&1 &
sleep 5
IP=$(hostname -I | awk '{print $1}')
echo "travel   -> http://$IP:$TRAVEL_PORT/mcp   $(curl -s localhost:$TRAVEL_PORT/health)"
echo "database -> http://$IP:$DB_PORT/mcp   $(curl -s localhost:$DB_PORT/health)"
echo "analytics-> http://$IP:$ANALYTICS_PORT/mcp   $(curl -s localhost:$ANALYTICS_PORT/health)"
echo "keys: dev-key-alice (all) | dev-key-bob (read) | dev-key-fr (analytics, France only)"
echo "logs: logs/*.log   |   stop: ./up.sh down"
