# Le MCP « base de données » — présentation pour le client

## 1. En une phrase

Un **serveur MCP** (Model Context Protocol) est un petit service qui expose vos données à des agents IA
(Gemini via Google ADK, Claude, ChatGPT/OpenAI, LangChain, Cursor…) via un **protocole standard**. On
l'écrit **une fois**, et **n'importe quel agent** peut l'interroger, avec une **clé API** et des
**permissions** par outil.

```
Agent IA (ADK, Claude, OpenAI…)  ──HTTPS + clé API──▶  MCP database  ──rôle lecture seule──▶  votre base
        "quel pays a le plus de clients ?"            list_tables / describe_table / query        Postgres, MySQL, SQLite…
```

L'agent **ne voit jamais** la base directement : il ne connaît que 4 opérations contrôlées, journalisées
et plafonnées.

## 2. Ce que l'agent peut faire (les « tools »)

| Outil | Rôle | Permission requise |
|---|---|---|
| `list_tables` | Liste les tables / vues et le nombre de colonnes | `db:read` |
| `describe_table(table)` | Colonnes, types, clé primaire, clés étrangères | `db:read` |
| `query(sql, params, limit)` | **Un seul `SELECT`**, paramètres bindés, résultat plafonné (200 lignes max) | `db:read` |
| `execute(sql, params)` | Un `INSERT` / `UPDATE` / `DELETE` unique | `db:write` |

Plus une **ressource** `schema://tables` (schéma complet lisible d'un coup) et un **prompt** `analyze`
(méthode : découvrir → décrire → requêter → répondre avec le SQL utilisé).

Le serveur porte aussi des **instructions** que l'agent lit au démarrage : « commence par `list_tables`,
puis `describe_table`, n'invente jamais un nom de colonne, utilise `WHERE` / `LIMIT` ».

## 3. Sécurité : 5 couches

1. **HTTPS** sur un sous-domaine (certificat automatique).
2. **Clé API** obligatoire (`Authorization: Bearer …` ou `X-API-Key`). Sans clé → `401`.
3. **Scopes** : une clé `db:read` **ne voit même pas** l'outil `execute` dans la liste des outils.
4. **Garde-fou SQL** : une instruction à la fois, `SELECT`/`WITH` uniquement dans `query`, mots-clés
   d'écriture/DDL refusés, `PRAGMA`/`ATTACH` refusés, résultats plafonnés, erreurs SQL résumées
   (jamais de trace interne).
5. **Rôle base de données en lecture seule** (`mcp_readonly`) : même si un garde-fou applicatif était
   contourné, la base refuse l'écriture. C'est la vraie barrière ; le reste est de la défense en profondeur.

Chaque appel produit une ligne d'**audit** : `{"tool": "query", "client": "agent-analytics", "ok": true, "ms": 9.7}`.
Un **rate-limit** par client (20 req/s par défaut) protège la base.

## 4. Comment c'est construit

Tout tient dans **un fichier**, [`servers/database/server.py`](../servers/database/server.py) (~150 lignes),
posé sur un socle commun `forge/` (~250 lignes) qui fournit transport, auth, audit, rate-limit, `/health`.

```python
mcp = create_server("database", instructions="SQL access… start with list_tables…")

@mcp.tool(annotations={"readOnlyHint": True}, auth=scopes("db:read"))
def query(sql: str, params: dict | None = None, limit: int = 50) -> dict:
    """Run a read-only SQL query…"""           # ← la docstring est ce que l'agent lit
    return _run(_assert_read_only(sql), params, limit)

@mcp.tool(annotations={"destructiveHint": True}, auth=scopes("db:write"))
def execute(sql: str, params: dict | None = None) -> dict: ...
```

- **Stack** : Python 3.12, FastMCP 4 (spec MCP 2026-07-28), SQLAlchemy 2 (Postgres, MySQL, SQLite, SQL Server…),
  Uvicorn, Docker.
- **Configuration 100 % par variables d'environnement** : `DATABASE_URL`, `MCP_API_KEYS`, `DB_MAX_ROWS`,
  `MCP_RATE_LIMIT_RPS`. Aucun secret dans le code.
- **Tests automatisés** (16, < 1 s) : auth, scopes, garde-fou SQL, plafond, erreurs.
- **Adaptable** : pour exposer un autre système (CRM, API interne, fichiers), on copie le modèle
  (`python -m forge new mon_mcp`) et on écrit les fonctions ; auth/transport/logs sont hérités.

## 5. Comment le consommer

Une fois déployé sur `https://mcp.votredomaine.com/mcp` (voir [DEPLOY.md](DEPLOY.md)), chaque
consommateur reçoit une clé et l'utilise ainsi :

**Google ADK (Gemini)**
```python
McpToolset(connection_params=StreamableHTTPConnectionParams(
    url="https://mcp.votredomaine.com/mcp",
    headers={"Authorization": "Bearer <clé>"}))
```

**Claude Code** — `claude mcp add --transport http db https://mcp.votredomaine.com/mcp --header "Authorization: Bearer <clé>"`

**Claude.ai / Claude Desktop** — Settings → Connectors → Add custom connector → URL + header.

**API Claude, OpenAI Agents SDK, LangChain, Cursor, VS Code, Python brut** — voir [CLIENTS.md](CLIENTS.md)
(tous testés ou documentés avec le code exact).

Exemple de conversation côté agent :

> **Utilisateur** : Quel est le chiffre d'affaires par catégorie de produit en 2026 ?
> **Agent** → `list_tables` → `describe_table("orders")`, `describe_table("products")` →
> `query("SELECT p.category, SUM(o.quantity*p.price_eur) AS revenue FROM orders o JOIN products p ON p.id=o.product_id WHERE o.order_date >= :d GROUP BY p.category ORDER BY revenue DESC", {"d": "2026-01-01"})`
> **Agent** : « Électronique 4 812 € ; bagagerie 3 105 € ; … (SQL utilisé : …) »

## 6. Ce qu'il reste à faire pour la mise en production

1. Créer un **rôle lecture seule** sur la base (script dans DEPLOY.md).
2. **Déployer** l'image Docker sur une instance (VPS, VM cloud) et pointer un sous-domaine dessus (30 min).
3. Générer une **clé par consommateur** (`openssl rand -hex 32`) et la transmettre par canal sûr.
4. (Optionnel) restreindre les tables exposées via une **vue** ou un **schéma dédié** que le rôle
   `mcp_readonly` est seul à voir.

Réponse à la question « il reste juste à déployer ce code sur une instance, lié à un sous-domaine, et
c'est consommable directement ? » → **Oui.** C'est exactement le contenu de [DEPLOY.md](DEPLOY.md).
