# Cas d'usage avancé : un MCP « analytics » qui expose les données d'une base

Fichier : [`servers/analytics/server.py`](../servers/analytics/server.py).
Serveur : `python -m forge run servers.analytics` (port 8003 via `./up.sh`).

## 1. Le problème que résout ce serveur

Donner un accès SQL brut à un agent (le MCP `database`) marche pour l'exploration. Pour un usage métier
quotidien, ça pose quatre problèmes :

1. **Définitions** : « chiffre d'affaires » = avec ou sans remboursements ? Prix au moment de la commande ?
   Chaque agent, chaque jour, peut répondre différemment.
2. **Sécurité par ligne** : le commercial France ne doit voir que la France. Impossible à garantir sur du SQL libre.
3. **Coût / performance** : un LLM écrit parfois des jointures cartésiennes.
4. **Fiabilité** : plus l'agent écrit de SQL, plus il se trompe.

La réponse classique en data engineering est une **couche sémantique** : des métriques et dimensions
nommées, le SQL est généré par le code, pas par le modèle. Ce serveur en est une version MCP.

## 2. Ce que l'agent voit

| Outil | Question métier | Scope |
|---|---|---|
| `kpis(start, end)` | « Comment va le business ce mois-ci ? » → CA net, commandes, panier moyen, taux de remboursement, nouveaux clients, clients actifs | `analytics:read` |
| `revenue(group_by, start, end, status, limit)` | « CA par mois / pays / catégorie / produit / statut » | `analytics:read` |
| `top_customers(n, offset, start, end)` | « Nos 10 meilleurs clients », paginé | `analytics:read` |
| `customer_360(customer_id)` | « Tout sur le client 42 » : profil, LTV, historique, catégorie préférée | `analytics:read` |
| `search_products(q, category, max_price_eur, limit, offset)` | « Quels produits bagagerie < 100 € ? » avec unités vendues | `analytics:read` |
| `sql(query, params, limit)` | Échappatoire lecture seule pour les questions non couvertes | `analytics:sql` |

Ressource `analytics://dictionary` : le **dictionnaire de données** (définitions de CA net, AOV, taux de
remboursement, sémantique des tables). Prompt `weekly_report(week_start)` : rapport hebdo en une page,
méthode imposée.

Instructions envoyées au LLM : « préfère les outils dédiés, lis le dictionnaire, `sql` en dernier recours,
certaines clés sont restreintes à des pays ».

## 3. Les cinq mécanismes avancés

### 3.1 Dimensions en liste blanche (zéro injection)

```python
DIMENSIONS = {"month": "substr(o.order_date,1,7)", "country": "c.country", "category": "p.category", …}
Dimension = Literal["month", "country", "category", "product", "status"]

def revenue(group_by: Dimension, …):
    dim = DIMENSIONS[group_by]
    sql = f"SELECT {dim} AS {group_by}, SUM(o.quantity*p.price_eur) … GROUP BY {dim}"
```

Le LLM choisit une **clé** ; pydantic refuse tout autre texte avant même l'appel (le test envoie
`"customers; DROP TABLE orders"` et obtient une erreur de validation). Le fragment SQL vient du code.
Toutes les valeurs (dates, statut, limites) passent en paramètres bindés `:name`.

### 3.2 Row-level security dérivée de la clé API

```python
def _allowed_countries():
    token = get_access_token()                       # AccessToken posé par forge
    return [s.split(":",1)[1] for s in token.scopes if s.startswith("country:")] or None
```

Une clé `dev-key-fr:agent-fr:analytics:read|country:FR` voit **uniquement la France** dans *tous* les outils :
`_where()` ajoute `c.country IN (:countries)` partout ; `customer_360` d'un client singapourien répond
« not found » ; `kpis` indique `scope_countries: ["FR"]` pour que l'agent sache qu'il est scopé.
L'outil `sql` est **interdit** aux clés restreintes (on ne peut pas garantir la RLS sur du SQL libre) et
de toute façon invisible sans le scope `analytics:sql`.

Aucune logique de tenant dans le code métier : ajouter un pays = ajouter un scope à une clé.

### 3.3 Pagination et plafonds

`top_customers` et `search_products` renvoient `next_offset` (ou `null` à la fin). L'agent enchaîne les
pages sans deviner. Chaque requête a un `LIMIT` bindé ; `MAX_ROWS = 500`.

### 3.4 Périodes sûres

`_period()` : défaut = 12 mois glissants ; `start > end` → `ToolError("start is after end")`, message que le
LLM corrige seul. Les dates sont des `datetime.date` pydantic : `2026-13-45` est refusé avant le SQL.

### 3.5 Cohérence garantie par les tests

`tests/test_analytics.py` vérifie que `revenue(by country)` et `revenue(by category)` donnent le même
total, que la somme des statuts non remboursés = `kpis.net_revenue_eur`, la pagination sans doublon, la
RLS de bout en bout **en HTTP avec de vraies clés**, et que `sql` disparaît de `tools/list` sans le scope.

## 4. Démo pas à pas

```bash
./up.sh                     # analytics -> http://<IP>:8003/mcp
# clés : dev-key-alice (tout, + sql) | dev-key-bob (lecture) | dev-key-fr (France seulement)

npx -y @modelcontextprotocol/inspector --cli http://localhost:8003/mcp --transport http \
  --header "Authorization: Bearer dev-key-fr" --method tools/list          # pas de `sql`
npx -y @modelcontextprotocol/inspector --cli http://localhost:8003/mcp --transport http \
  --header "Authorization: Bearer dev-key-fr" --method tools/call --tool-name kpis \
  --tool-arg start=2026-01-01 --tool-arg end=2026-12-31                    # scope_countries: ["FR"]
```

Avec Google ADK (même code que les autres serveurs, juste l'URL) :

```python
McpToolset(connection_params=StreamableHTTPConnectionParams(
    url="http://<IP>:8003/mcp", headers={"Authorization": "Bearer dev-key-alice"}))
```

Questions à poser à l'agent :
- « Quel est notre panier moyen et notre taux de remboursement sur 2026 ? » → `kpis`
- « CA par catégorie, puis les 5 meilleurs clients » → `revenue(group_by="category")`, `top_customers(n=5)`
- « Fais-moi le rapport de la semaine du 2026-08-24 » → prompt `weekly_report`
- « Combien de clients ont commandé dans 3 catégories différentes ? » → non couvert → `sql` (clé alice)

## 5. Adapter à votre base

1. Copiez `servers/analytics` → `servers/mon_domaine` (`python -m forge new` pour un squelette vide).
2. Remplacez `_FROM` et `DIMENSIONS` par vos tables/jointures ; gardez les valeurs en paramètres bindés.
3. Écrivez `DICTIONARY` **avec le métier** : c'est le document le plus lu par l'agent.
4. Choisissez la clé de RLS (`country:`, `tenant:`, `region:`) : seule `_allowed_countries()` change.
5. Un outil = une question métier récurrente. Si une question revient trois fois en `sql`, promouvez-la.
6. Ajoutez un test « cohérence » (deux découpages, même total) pour chaque nouvelle métrique.

## 6. Ce que ça donne en production

- Le serveur est **stateless** : n scalable derrière le proxy, une clé = un consommateur = une ligne
  d'audit lisible (`{"tool":"kpis","client":"agent-fr","ok":true,"ms":6.1}`).
- La base est protégée trois fois : SQL généré par le code, rôle DB lecture seule, timeout serveur.
- Les métriques ont **une** définition, versionnée avec le code, testée.
- Un nouveau canal (Claude, ChatGPT, Slack via ADK…) = la même URL et une nouvelle clé.
