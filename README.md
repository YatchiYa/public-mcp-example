# mcp-forge

Un kit minimal et robuste pour créer des **serveurs MCP** (Model Context Protocol) de qualité
production, appelables depuis **Google ADK**, Claude Desktop/Code, ou n'importe quel client MCP.

- **FastMCP 4** sur le SDK MCP 2.x → spec **2026-07-28** (Streamable HTTP, négociation de version
  par connexion : les clients plus anciens comme Google ADK fonctionnent aussi, testé).
- **Un fichier = un serveur.** Le transport, l'auth, les logs d'audit, le rate-limit et `/health`
  sont hérités de `forge`, pilotés par variables d'environnement.
- **Auth par clé API** (`Authorization: Bearer` ou `X-API-Key`) avec **scopes par outil**, ou pas
  d'auth du tout : même code.
- Deux serveurs d'exemple complets et testés :
  - `servers/travel` — géocodage, météo, qualité de l'air, devises, heure locale (APIs publiques
    gratuites, zéro clé).
  - `servers/database` — expose **votre base de données** (SQLite / Postgres / MySQL via SQLAlchemy)
    avec SQL en lecture seule garanti, écriture réservée au scope `db:write`.

```
forge/                core (config, auth, middleware, CLI)         ~250 lignes
servers/travel/       exemple sans clé  (docs/USECASE_TRAVEL.md)
servers/database/     exemple base de données + auth  (docs/USECASE_DATABASE.md, DATABASE_MCP_WALKTHROUGH.md)
servers/analytics/    cas d'usage avancé : couche sémantique, RLS par clé, pagination (docs/USECASE_ANALYTICS.md)
servers/_template/    modèle copié par `python -m forge new`
tests/                22 tests, < 1 s, sans réseau
examples/adk_agent/   agent Google ADK (+ smoke test sans LLM)
examples/clients/       OpenAI Agents SDK, LangChain, Claude API, Gemini, SDK mcp brut, Cursor, VS Code, Claude Desktop
docs/CLIENTS.md         guide « appeler depuis n'importe quelle plateforme » + dépannage
docs/DATABASE_MCP.md    présentation client du MCP base de données
docs/DEPLOY.md          déploiement instance + sous-domaine HTTPS (Docker + Caddy)
deploy/                 docker-compose.prod.yml, Caddyfile, .env.example
.mcp.json               enregistrement Claude Code (scope projet)
Dockerfile, docker-compose.yml, Makefile, .env.example
```

## 1. Démarrage (2 minutes)

```bash
uv sync                                   # Python 3.12 + deps (uv crée .venv)
uv run pytest -q                          # 22 passed
uv run python -m forge inspect servers.travel   # liste tools / resources / prompts

# Serveur HTTP sans auth (dev)
MCP_TRANSPORT=http uv run python -m forge run servers.travel
curl localhost:8000/health
```

Serveur HTTP **avec** clés API et scopes :

```bash
export MCP_TRANSPORT=http
export MCP_API_KEYS="dev-key-alice:alice:db:read|db:write;dev-key-bob:bob:db:read"
uv run python -m servers.database.seed    # crée demo.db (clients/produits/commandes)
uv run python -m forge run servers.database
```

Format de `MCP_API_KEYS` : `clé:client_id:scope1|scope2;clé2:client2` (scopes optionnels).
Sans clé → `401`. Un client sans le scope d'un outil **ne voit même pas** l'outil dans `tools/list`.

Mode **stdio** (Claude Desktop, CLIs locaux) : `MCP_TRANSPORT=stdio` (défaut), aucune auth.
Voir `examples/clients/claude_desktop_config.json`.

## 2. Appeler depuis Google ADK (et les autres)

**Tous les clients** (Claude Code, Claude Desktop, API Claude, Gemini, OpenAI Agents SDK, LangChain,
Cursor, VS Code, SDK brut, Inspector) sont couverts dans [docs/CLIENTS.md](docs/CLIENTS.md), avec ce
qui a été testé ici. Google ADK est simplement l'exemple le plus complet :

ADK épingle le SDK MCP en 1.x, FastMCP 4 exige le 2.x : l'agent a donc **son propre venv**
(c'est de toute façon un autre processus / conteneur).

```bash
cd examples/adk_agent
uv venv && source .venv/bin/activate && uv pip install -r requirements.txt
export MCP_URL=http://localhost:8000/mcp MCP_API_KEY=dev-key-alice
python smoke.py            # sans LLM : liste et appelle un outil via McpToolset
export GOOGLE_API_KEY=...  # puis :
adk web                    # ou adk run travel_agent
```

Le cœur de `travel_agent/agent.py` :

```python
McpToolset(connection_params=StreamableHTTPConnectionParams(
    url=MCP_URL, headers={"Authorization": f"Bearer {MCP_API_KEY}"}, timeout=15))
```

Résultat vérifié : ADK (SDK MCP 1.29, protocole 2025-11-25) ↔ ce serveur (FastMCP 4, 2026-07-28) :
`list_tools`, `call_tool`, 401 propre avec mauvaise clé, scopes respectés.

## 3. Créer votre propre serveur MCP

```bash
uv run python -m forge new inventory        # copie servers/_template -> servers/inventory
$EDITOR servers/inventory/server.py
uv run python -m forge inspect servers.inventory
MCP_TRANSPORT=http uv run python -m forge run servers.inventory
```

Le fichier généré tient en 30 lignes :

```python
from forge import create_server, run, scopes
from fastmcp.exceptions import ToolError

mcp = create_server("inventory", instructions="Quand utiliser ce serveur, pour le LLM.")

@mcp.tool(annotations={"readOnlyHint": True})
def stock(sku: str) -> dict:
    """Docstring = description lue par le LLM. Types/pydantic = schéma JSON validé."""
    if not sku:
        raise ToolError("sku requis")          # message renvoyé tel quel au LLM
    return {"sku": sku, "qty": 42}

@mcp.tool(auth=scopes("inventory:write"))       # visible/appelable seulement avec ce scope
def restock(sku: str, qty: int) -> dict: ...

if __name__ == "__main__":
    run(mcp)
```

Règles qui font la différence pour un agent :
- **`instructions`** courtes et actionnables ("appelle X d'abord, puis Y").
- **Docstrings** et `Annotated[..., Field(description=...)]` : c'est tout ce que le LLM voit.
- **`ToolError`** avec un message qui dit quoi corriger ; tout le reste est masqué
  (`MCP_MASK_ERRORS=true`) et loggé côté serveur avec traceback.
- **Annotations** `readOnlyHint` / `destructiveHint` / `openWorldHint` : les clients s'en servent
  pour demander confirmation.
- Fonctions **sync** = exécutées dans un threadpool (OK pour SQL) ; **async** pour HTTP.

### Exposer votre base de données

```bash
export DATABASE_URL="postgresql+psycopg://readonly_user:pwd@host/db"   # uv add psycopg
export MCP_API_KEYS="cle-prod:analytics-agent:db:read"
MCP_TRANSPORT=http uv run python -m forge run servers.database
```

Garde-fous inclus : une seule instruction, `SELECT`/`WITH` uniquement, mots-clés d'écriture
refusés, résultats plafonnés (`DB_MAX_ROWS`), paramètres bindés (`:name`), erreurs SQL résumées.
**Utilisez quand même un rôle DB en lecture seule** : le garde-fou applicatif est une seconde
ligne de défense, pas la première.

## 4. Ce que `forge` fait pour vous

| Fonction | Où | Réglage |
|---|---|---|
| Transport stdio / Streamable HTTP | `forge.run` | `MCP_TRANSPORT`, `MCP_HOST`, `MCP_PORT`, `MCP_PATH` |
| Clés API + scopes | `forge.auth.ApiKeyVerifier`, `forge.scopes` | `MCP_API_KEYS` |
| `X-API-Key` → `Authorization: Bearer` | `forge.auth.ApiKeyHeaderMiddleware` | automatique |
| Audit JSON par appel (client, outil, ms, ok/erreur) | `forge.middleware.AuditMiddleware` | `MCP_LOG_LEVEL` |
| Rate limit par client (token bucket) | FastMCP `RateLimitingMiddleware` | `MCP_RATE_LIMIT_RPS` |
| Masquage des erreurs internes | FastMCP `mask_error_details` | `MCP_MASK_ERRORS` |
| `GET /health` | `forge.build_http_app` | — |

Pour passer d'une clé API à **JWT / OAuth** : remplacez `ApiKeyVerifier` par
`fastmcp.server.auth.providers.jwt.JWTVerifier(jwks_uri=..., issuer=..., audience=...)` dans
`create_server` ; les serveurs et leurs `scopes(...)` ne changent pas.

## 5. Tests

```bash
uv run pytest -q
```

- `tests/test_forge.py` — parsing des clés, 401 sans clé, Bearer et X-API-Key, scopes (outil
  caché + refus), `/health`, mode ouvert. Le client FastMCP parle du vrai Streamable HTTP à l'app
  ASGI **en mémoire** (aucun port ouvert).
- `tests/test_travel.py` — flux complet avec `httpx.MockTransport` (aucun appel réseau).
- `tests/test_database.py` — SQLite temporaire, garde-fou SQL, paramètres, plafond, écriture.

## 6. Docker

```bash
docker compose up --build          # travel :8000, database :8001
docker build --build-arg SERVER=servers.database -t my-mcp .
```

## 7. Structure d'un appel

```
Agent (ADK / Claude) ──HTTP POST /mcp (Bearer key)──▶ uvicorn
   ▶ ApiKeyHeaderMiddleware (X-API-Key → Bearer)
   ▶ FastMCP BearerAuth → ApiKeyVerifier.verify_token → AccessToken(client_id, scopes)
   ▶ ErrorHandlingMiddleware → AuditMiddleware → RateLimiting
   ▶ scopes(...) check → votre fonction (validation pydantic des arguments)
   ◀ structuredContent + content (texte) ; ToolError → message ; autre → "Internal error"
```

## Sources
- Spec MCP 2026-07-28 : https://blog.modelcontextprotocol.io/posts/2026-07-28/
- FastMCP : https://gofastmcp.com (auth, middleware, tests)
- Google ADK MCP tools : https://adk.dev/tools-custom/mcp-tools/
- Open-Meteo (géocodage, météo, air) : https://open-meteo.com — Frankfurter (devises) : https://frankfurter.dev
