# Appeler votre serveur MCP depuis n'importe quelle plateforme

Ce guide couvre **tous** les clients courants. Google ADK n'est qu'un cas parmi d'autres : un serveur
MCP est un contrat standard, chaque client ne change que la *façon de fournir l'URL et la clé*.

> ✅ = testé dans ce repo contre le serveur `travel-intel` (FastMCP 4, spec 2026-07-28) le 2026-09-02.
> 📄 = configuration reprise de la doc officielle, non exécutée ici (nécessite un compte / un domaine public).

## 0. Savoir « quoi est quoi »

| Terme | Ce que c'est | Dans ce projet |
|---|---|---|
| **Serveur MCP** | Un processus qui expose des *tools*, *resources*, *prompts* via JSON-RPC | `python -m forge run servers.<nom>` |
| **Transport stdio** | Le client lance le serveur en sous-processus et parle sur stdin/stdout. Local, pas d'auth. | `MCP_TRANSPORT=stdio` (défaut) |
| **Transport Streamable HTTP** | Un seul endpoint HTTP (`POST /mcp`), réponses JSON ou SSE. Distant, multi-clients, auth. | `MCP_TRANSPORT=http` → `http://host:8000/mcp` |
| **SSE (ancien)** | Transport HTTP historique, déprécié depuis la spec 2025-03-26. | non exposé (inutile) |
| **Clé API** | Secret envoyé en `Authorization: Bearer <clé>` (standard) ou `X-API-Key: <clé>` | `MCP_API_KEYS=clé:client:scope1\|scope2` |
| **Scope** | Permission attachée à une clé ; un outil peut en exiger un | `@mcp.tool(auth=scopes("db:write"))` |
| **Tool** | Fonction appelable par le LLM, avec schéma JSON d'entrée/sortie | `@mcp.tool` |
| **Resource** | Donnée lisible par URI (contexte), pas d'exécution | `@mcp.resource("schema://tables")` |
| **Prompt** | Template de message réutilisable | `@mcp.prompt` |
| **`structuredContent`** | Sortie typée d'un tool (dict/list) ; `content[].text` = version texte | tous les tools renvoient les deux |
| **`/health`** | Endpoint HTTP hors MCP pour les load-balancers / Docker | `GET /health` |
| **Client local vs. cloud** | Un client *sur votre machine* (Claude Code, ADK local, Cursor) joint `localhost`. Un client *hébergé* (API Claude, Gemini Interactions, claude.ai) exige une URL **https publique**. | tunnel : `cloudflared tunnel --url http://localhost:8000` ou déploiement Docker |

Démarrer le serveur pour tous les exemples HTTP ci-dessous :

```bash
export MCP_TRANSPORT=http MCP_PORT=8000
export MCP_API_KEYS="dev-key-alice:alice:db:read|db:write;dev-key-bob:bob:db:read"
uv run python -m forge run servers.travel        # -> http://localhost:8000/mcp
curl -s localhost:8000/health                    # {"status":"ok","server":"travel-intel","auth":true}
```

---

## 1. Claude Code ✅

```bash
# HTTP distant (projet : écrit .mcp.json, partageable via git)
claude mcp add --transport http --scope project travel-intel http://localhost:8000/mcp \
  --header "Authorization: Bearer dev-key-alice"

# ou utilisateur (global) :  --scope user
# stdio local (aucune auth, Claude Code lance le process) :
claude mcp add travel-local -e MCP_TRANSPORT=stdio -- uv run --directory "$PWD" python -m forge run servers.travel

claude mcp list          # état de santé de chaque serveur
```

`.mcp.json` produit (déjà présent dans ce repo) :

```json
{ "mcpServers": { "travel-intel": { "type": "http", "url": "http://localhost:8000/mcp",
  "headers": { "Authorization": "Bearer dev-key-alice" } } } }
```

Au premier lancement de `claude` dans le projet, approuvez le serveur (« Pending approval »).
Puis dans la session : `/mcp` pour voir les outils ; le modèle les appelle comme `mcp__travel-intel__get_weather`.

## 2. Claude Desktop / claude.ai 📄

**Distant (recommandé)** : *Settings → Connectors → Add custom connector* → URL `https://votre-hote/mcp`,
auth **None**, puis ajoutez `Authorization: Bearer <clé>` dans **Request headers**. URL https publique requise.

**Local stdio** ou **pont vers HTTP local** via `claude_desktop_config.json`
(`~/Library/Application Support/Claude/` sur macOS, `%APPDATA%\Claude\` sur Windows) :
voir [`examples/clients/claude_desktop_config.json`](../examples/clients/claude_desktop_config.json)
(`uv run … forge run` en stdio, ou `npx mcp-remote <url> --header "Authorization: Bearer …"`).

## 3. Claude API (Messages API, connecteur MCP intégré) 📄

Aucun client MCP à écrire : l'API Anthropic appelle votre serveur. **https public obligatoire.**

```python
client.beta.messages.create(
    model="claude-opus-5", max_tokens=1000,
    messages=[{"role": "user", "content": "Météo à Kyoto ?"}],
    mcp_servers=[{"type": "url", "url": "https://votre-hote/mcp", "name": "travel",
                  "authorization_token": "dev-key-alice"}],      # -> Authorization: Bearer
    tools=[{"type": "mcp_toolset", "mcp_server_name": "travel"}],
    betas=["mcp-client-2025-11-20"],
)
```

Fichier complet : [`examples/clients/anthropic_messages_api.py`](../examples/clients/anthropic_messages_api.py).
Allowlist / denylist d'outils via `default_config` / `configs` dans le `mcp_toolset`.

## 4. Google ADK ✅

```python
from google.adk.agents.llm_agent import LlmAgent
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

root_agent = LlmAgent(
    model="gemini-2.5-flash", name="travel_assistant", instruction="…",
    tools=[McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url="http://localhost:8000/mcp",
            headers={"Authorization": "Bearer dev-key-alice"}, timeout=15),
        tool_filter=["geocode_place", "get_weather"],   # optionnel
    )],
)
```

- Venv séparé : `google-adk[mcp]` épingle le SDK MCP 1.x (FastMCP 4 = 2.x). Voir
  [`examples/adk_agent/`](../examples/adk_agent/) (`smoke.py` = test sans LLM, `adk web` = UI).
- Interop vérifiée : ADK 2.8 / SDK MCP 1.29 (protocole 2025-11-25) ↔ serveur 2026-07-28.
- Clé par utilisateur : `McpToolset(header_provider=lambda ctx: {"Authorization": f"Bearer {ctx.state['key']}"})`.

## 5. Gemini API (Interactions API, MCP distant) 📄

Google appelle votre serveur → **https public**, nom **snake_case** (pas de `-`).

```python
client.interactions.create(model="gemini-2.5-flash", input="…",
    tools=[{"type": "mcp_server", "name": "travel_intel", "url": "https://votre-hote/mcp",
            "headers": {"Authorization": "Bearer dev-key-alice"}}])
```

Fichier : [`examples/clients/gemini_interactions_api.py`](../examples/clients/gemini_interactions_api.py).

## 6. OpenAI Agents SDK ✅

```python
from agents import Agent, Runner
from agents.mcp import MCPServerStreamableHttp

async with MCPServerStreamableHttp(name="travel",
        params={"url": "http://localhost:8000/mcp", "headers": {"Authorization": "Bearer dev-key-alice"}, "timeout": 15},
        cache_tools_list=True, max_retry_attempts=3) as server:
    agent = Agent(name="Travel", instructions="Use the MCP tools.", mcp_servers=[server])
    print((await Runner.run(agent, "Il pleut à Tokyo ?")).final_output)
```

Fichier : [`examples/clients/openai_agents_sdk.py`](../examples/clients/openai_agents_sdk.py).

## 7. LangChain / LangGraph ✅

```python
from langchain_mcp_adapters.client import MultiServerMCPClient
client = MultiServerMCPClient({"travel": {"transport": "http", "url": "http://localhost:8000/mcp",
                                          "headers": {"Authorization": "Bearer dev-key-alice"}}})
tools = await client.get_tools()            # -> outils LangChain, utilisables dans create_agent / LangGraph
```

Fichier : [`examples/clients/langchain_agent.py`](../examples/clients/langchain_agent.py).

## 8. Python sans framework

**SDK officiel `mcp` (1.x ou 2.x)** ✅ — [`examples/clients/raw_mcp_sdk.py`](../examples/clients/raw_mcp_sdk.py) :

```python
async with streamablehttp_client(url, headers={"Authorization": "Bearer …"}) as (r, w, _):
    async with ClientSession(r, w) as s:
        await s.initialize(); print(await s.list_tools())
```

**Client FastMCP** ✅ (même venv que le serveur) — [`examples/quick_client.py`](../examples/quick_client.py) :

```python
async with Client("http://localhost:8000/mcp", auth="dev-key-alice") as c:
    print((await c.call_tool("geocode_place", {"query": "Tokyo"})).data)
```

## 9. Cursor / VS Code (Copilot) 📄

- Cursor : `.cursor/mcp.json` → [`examples/clients/cursor.mcp.json`](../examples/clients/cursor.mcp.json)
  (`${env:MCP_API_KEY}` interpolé).
- VS Code : `.vscode/mcp.json` → [`examples/clients/vscode.mcp.json`](../examples/clients/vscode.mcp.json)
  (`inputs` + `promptString password` pour ne jamais committer la clé).

## 10. Outils de debug

**MCP Inspector** ✅ (UI web ou CLI) :

```bash
npx -y @modelcontextprotocol/inspector --cli http://localhost:8000/mcp --transport http \
  --header "Authorization: Bearer dev-key-alice" --method tools/list
npx -y @modelcontextprotocol/inspector            # UI : Transport = Streamable HTTP, URL, header
```

**curl** (un appel brut, sans session) :

```bash
curl -s localhost:8000/mcp -H 'Authorization: Bearer dev-key-alice' -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' -H 'Mcp-Method: tools/list' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

(Les clients plus anciens font d'abord `initialize` ; le serveur négocie la version par connexion.)

---

## Dépannage

| Symptôme | Cause | Fix |
|---|---|---|
| `401 invalid_token` | clé absente/incorrecte, ou `MCP_API_KEYS` non défini côté serveur | vérifier `curl /health` → `"auth": true`, et le header |
| L'outil n'apparaît pas dans `tools/list` | la clé n'a pas le scope exigé par `scopes(...)` | ajouter le scope à la clé |
| `404` sur `/mcp` | mauvais `MCP_PATH` ou URL sans `/mcp` | `curl /health` puis corriger l'URL |
| `406 Not Acceptable` | header `Accept` manquant (curl brut) | `Accept: application/json, text/event-stream` |
| Le client cloud ne joint pas le serveur | `localhost` n'est pas public | déployer (Docker) ou `cloudflared tunnel --url http://localhost:8000` |
| Erreur `Internal error` sans détail | exception non prévue, masquée (`MCP_MASK_ERRORS=true`) | lire les logs serveur (traceback complet + ligne `forge.audit`) |
| ADK : `No module named mcp.shared.session` | ADK installé dans le venv FastMCP 4 (SDK 2.x) | venv séparé avec `google-adk[mcp]` |
| Claude Desktop n'accepte pas `http://localhost` avec header | les connecteurs custom exigent https | pont `mcp-remote` (stdio) ou tunnel https |
