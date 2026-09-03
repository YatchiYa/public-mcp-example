# Cas d'usage : le MCP `database`, accès SQL contrôlé pour un agent

Fichier : [`servers/database/server.py`](../servers/database/server.py).
Serveur : `python -m forge run servers.database` (port 8001 via `./up.sh`).
Explication ligne par ligne : [DATABASE_MCP_WALKTHROUGH.md](DATABASE_MCP_WALKTHROUGH.md). Présentation client : [DATABASE_MCP.md](DATABASE_MCP.md).

## 1. Le problème que résout ce serveur

Un agent IA doit répondre à des questions dont la réponse est **dans votre base** (« combien de commandes
en attente ? », « quels clients n'ont pas commandé depuis 6 mois ? »). Les options habituelles :

- **Copier des extraits dans le prompt** : figé, incomplet, dangereux (fuite de données).
- **Donner un accès direct à la base** : l'agent peut tout casser, rien n'est tracé.
- **Écrire une API métier par question** : des semaines de travail avant la première réponse.

Le MCP `database` donne à l'agent un accès **générique, en lecture seule, plafonné et audité**, en une
variable d'environnement (`DATABASE_URL`). C'est l'outil d'**exploration** ; pour des questions métier
récurrentes on passe ensuite à une couche sémantique ([USECASE_ANALYTICS.md](USECASE_ANALYTICS.md)).

## 2. Ce que l'agent voit

| Outil | Question typique | Scope |
|---|---|---|
| `list_tables()` | « Qu'est-ce qu'il y a dans cette base ? » | `db:read` |
| `describe_table(table)` | « Quelles colonnes, quelles clés pour joindre ? » | `db:read` |
| `query(sql, params, limit)` | « SELECT … » un seul, paramétré, ≤ 200 lignes | `db:read` |
| `execute(sql, params)` | « Marque la commande 42 comme expédiée » | `db:write` |

Ressource `schema://tables` (schéma complet en une lecture). Prompt `analyze(question)` (méthode
découvrir → décrire → requêter → citer le SQL).

Instructions envoyées au LLM : « commence par `list_tables`, puis `describe_table`, n'invente jamais un nom
de colonne, résultats plafonnés : utilise `WHERE` / `LIMIT` / agrégats ».

## 3. Les mécanismes de sécurité

### 3.1 Trois barrières indépendantes contre l'écriture

1. **Garde-fou texte** : une instruction, doit commencer par `SELECT`/`WITH`, aucun mot-clé
   d'écriture/DDL, commentaires retirés avant analyse.
2. **Connexion en lecture seule au niveau base** : Postgres `readonly`, SQLite `PRAGMA query_only`.
3. **Rôle DB `mcp_readonly`** (production) : la base elle-même refuse tout ce qui n'est pas `SELECT`.

Le regex peut avoir un trou ; les deux autres non. On les empile.

### 3.2 Protection de la base

- `statement_timeout` Postgres : une requête > 15 s est tuée par le serveur.
- Pool borné (5 + 5 connexions), `pool_pre_ping`, `pool_recycle` : pas d'épuisement de connexions, pas
  d'erreur sur connexion morte.
- `fetchmany(limit + 1)` : jamais de `fetchall`, un `SELECT *` sur une table de 10 M lignes coûte 201 lignes.
- Rate-limit par client (`MCP_RATE_LIMIT_RPS`).

### 3.3 Écriture uniquement pour qui a le droit

`execute` est marqué `destructiveHint` (les clients demandent confirmation) et exige `db:write`. Une clé
sans ce scope **ne voit pas** l'outil dans `tools/list`. `INSERT/UPDATE/DELETE` uniquement, une instruction.

### 3.4 Sorties pensées pour le LLM

- `truncated: true` explicite → l'agent affine au lieu de croire qu'il a tout vu.
- Erreurs SQL réduites à leur première ligne (`SQL error: no such column: foo`) → l'agent corrige seul.
- `jsonable()` : `Decimal`, dates, UUID, bytes convertis ; aucune réponse ne plante sur un type driver.
- Paramètres bindés `:name` dans la signature → l'agent est incité à ne pas concaténer de valeurs.

### 3.5 Audit

Chaque appel : `{"tool":"query","client":"agent-analytics","ok":true,"ms":9.7}` (ou `ok:false` + erreur).
Qui a lu quoi, quand, combien de temps.

## 4. Démo pas à pas

```bash
./up.sh                                    # database -> http://<IP>:8001/mcp
# clés : dev-key-alice (db:read|db:write) | dev-key-bob (db:read)

# bob : 3 outils, pas d'execute
npx -y @modelcontextprotocol/inspector --cli http://localhost:8001/mcp --transport http \
  --header "Authorization: Bearer dev-key-bob" --method tools/list | grep '"name"'

# une requête paramétrée
npx -y @modelcontextprotocol/inspector --cli http://localhost:8001/mcp --transport http \
  --header "Authorization: Bearer dev-key-bob" --method tools/call --tool-name query \
  --tool-arg 'sql=SELECT country, COUNT(*) AS n FROM customers GROUP BY country ORDER BY n DESC' --tool-arg limit=5

# tentative d'écriture via query -> refusée avec un message clair
npx -y @modelcontextprotocol/inspector --cli http://localhost:8001/mcp --transport http \
  --header "Authorization: Bearer dev-key-bob" --method tools/call --tool-name query \
  --tool-arg 'sql=DELETE FROM orders'
```

Avec Google ADK :

```python
McpToolset(connection_params=StreamableHTTPConnectionParams(
    url="http://<IP>:8001/mcp", headers={"Authorization": "Bearer dev-key-bob"}))
```

Questions à poser à l'agent :
- « Quelles tables existent et comment sont-elles liées ? » → `list_tables`, `describe_table`
- « Top 3 des pays par nombre de clients » → `query`
- « Chiffre d'affaires par catégorie de produit en 2026 » → `describe_table` ×2 puis `query` avec jointure
- « Passe la commande 12 en statut shipped » → `execute` (clé alice seulement ; refusé pour bob)

## 5. Adapter à votre base

1. `DATABASE_URL=postgresql+psycopg://mcp_readonly:…@host/db` (ou MySQL, SQL Server, SQLite).
   Créez le rôle lecture seule ([DEPLOY.md](DEPLOY.md) §1).
2. Limitez la surface : un schéma `mcp` avec des **vues** (colonnes sensibles exclues), `GRANT SELECT`
   sur ce schéma seulement. Le MCP ne verra que ça.
3. Réglez `DB_MAX_ROWS` (défaut 200) et `DB_STATEMENT_TIMEOUT_MS` (15 000) selon la base.
4. Une clé par consommateur, `db:read` seul par défaut ; `db:write` uniquement pour un agent d'action
   avec confirmation humaine côté client.
5. Adaptez `instructions` : citez les tables importantes et les pièges (« `orders.status` vaut
   paid|shipped|refunded », « les montants sont en centimes »). C'est gratuit et ça divise les erreurs.

## 6. Ce que ça donne en production

- Temps de mise en place : une variable d'environnement, un rôle DB, un déploiement Docker.
- Aucune requête non tracée, aucune écriture sans scope, aucune requête > 15 s, aucune réponse > 200 lignes.
- Le même serveur sert Claude, Gemini/ADK, ChatGPT, Cursor : une URL, une clé par canal.
- Quand une question revient souvent, on la « promeut » en outil dédié dans un serveur `analytics`.
