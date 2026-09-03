# Le MCP `database`, ligne par ligne

Fichier : [`servers/database/server.py`](../servers/database/server.py) (~190 lignes).
Objectif : donner à un agent un accès **contrôlé** à une base relationnelle, quelle qu'elle soit.

## 1. Vue d'ensemble

```
tools/call "query" ──▶ forge (auth, scope db:read, audit) ──▶ query()
                                                               ├─ _assert_read_only(sql)   garde-fou texte
                                                               └─ _run(sql, params, limit, readonly=True)
                                                                    ├─ connexion READ ONLY (Postgres flag / SQLite PRAGMA)
                                                                    ├─ statement_timeout côté serveur (Postgres)
                                                                    ├─ fetchmany(limit+1) → truncated
                                                                    └─ jsonable() sur chaque valeur
```

## 2. Configuration et moteur

```python
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./demo.db")
MAX_ROWS = int(os.environ.get("DB_MAX_ROWS", "200"))
STATEMENT_TIMEOUT_MS = int(os.environ.get("DB_STATEMENT_TIMEOUT_MS", "15000"))
```

Trois variables, aucune valeur en dur. `DATABASE_URL` est une URL SQLAlchemy : `postgresql+psycopg://…`,
`mysql+pymysql://…`, `sqlite:///…`, `mssql+pyodbc://…`. Changer de base = changer une variable.

```python
def engine_kwargs(url):
    kw = {"pool_pre_ping": True, "future": True}
    if url.startswith("postgresql"):
        kw["connect_args"] = {"options": f"-c statement_timeout={STATEMENT_TIMEOUT_MS}"}
        kw.update(pool_size=5, max_overflow=5, pool_recycle=1800)
```

- `pool_pre_ping` : une connexion morte (redémarrage DB, firewall) est détectée et remplacée, pas d'erreur
  remontée à l'agent.
- `statement_timeout` : Postgres tue lui-même toute requête > 15 s. Un agent qui écrit un `JOIN` cartésien
  ne peut pas bloquer la base.
- Pool borné (5 + 5) : un serveur MCP ne doit jamais épuiser les connexions de la base de production.
- `pool_recycle` : recycle les connexions avant que le serveur DB ou un proxy ne les coupe.

`readiness()` fait un `SELECT 1` ; `GET /health` renvoie `503 degraded` si la base est injoignable, ce qui
permet au load-balancer ou à Docker de sortir l'instance du trafic.

## 3. Le serveur et ses instructions

```python
mcp = create_server("database", instructions="SQL access… Start with list_tables, then describe_table, then
                    write a SELECT… Never guess column names. Results are capped at 200 rows…", readiness=readiness)
```

`instructions` est envoyé au client à la connexion : c'est le « mode d'emploi » que le LLM lit avant de
choisir un outil. On y met l'ordre des appels et les contraintes, pas de la prose.

## 4. Le garde-fou SQL

```python
_FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|create|truncate|replace|grant|revoke|attach|detach|pragma|vacuum|copy|call|do|merge|lock)\b", re.I)

def _assert_read_only(sql):
    sql = _strip_comments(sql)            # retire /* … */ et -- … (cachettes classiques)
    if ";" in sql: raise ToolError("Only one statement per query")
    if not re.match(r"^(select|with)\b", sql, re.I): raise ToolError("Only SELECT…")
    if _FORBIDDEN.search(sql): raise ToolError("Statement contains a write/DDL keyword…")
    return sql
```

Trois règles simples : une seule instruction, doit commencer par `SELECT`/`WITH`, aucun mot-clé
d'écriture nulle part (même dans une CTE). Les messages `ToolError` sont **écrits pour le LLM** : il
comprend l'erreur et se corrige au tour suivant.

Ce garde-fou est volontairement simple ; il n'est **pas** la barrière principale. Les deux suivantes le sont.

## 5. Connexion en lecture seule au niveau base

```python
conn = conn.execution_options(postgresql_readonly=True)      # psycopg : SET default_transaction_read_only
if engine.dialect.name == "sqlite":
    conn.exec_driver_sql("PRAGMA query_only = ON")           # SQLite refuse toute écriture
```

Même si une écriture passait le regex, la **base** la refuse. Sur MySQL/SQL Server, cette garantie vient du
**rôle en lecture seule** (`GRANT SELECT` uniquement) décrit dans [DEPLOY.md](DEPLOY.md), qu'on utilise de
toute façon partout en production. Le `PRAGMA` est remis à `OFF` dans un `finally` parce que la connexion
retourne dans le pool et peut servir `execute` ensuite.

## 6. Exécution, plafond, sérialisation

```python
rows = result.mappings().fetchmany(limit + 1)     # on lit limit+1 pour savoir s'il y en avait plus
truncated = len(rows) > limit
return {"columns": [...], "rows": [{k: jsonable(v) …}], "row_count": …, "truncated": truncated}
```

- On ne fait jamais `fetchall()` : un `SELECT * FROM events` sur 10 M de lignes ne charge que 201 lignes.
- `truncated: true` dit explicitement à l'agent « affine ta requête » au lieu de le laisser croire qu'il a
  tout vu.
- `jsonable()` convertit ce que les drivers renvoient et que JSON ne connaît pas : `Decimal` → float,
  `date/datetime` → ISO 8601, `UUID` → str, `bytes` → base64. Sans ça, un `NUMERIC` Postgres ferait planter
  la réponse.
- Les erreurs SQLAlchemy sont réduites à leur première ligne (`SQL error: no such column: foo`) :
  utile au LLM, sans stack trace ni DSN.

## 7. Les quatre outils

| Outil | Points notables |
|---|---|
| `list_tables()` | Via `sqlalchemy.inspect` : fonctionne sur tous les dialectes, inclut les vues. |
| `describe_table(table)` | `Field(pattern=^[A-Za-z_][A-Za-z0-9_]*$)` : le nom est validé **avant** d'atteindre le code (pydantic), pas d'injection possible via ce paramètre. Renvoie PK et FK : l'agent sait comment joindre. |
| `query(sql, params, limit)` | `params` = paramètres bindés `:name` → l'agent est poussé à ne pas concaténer de valeurs. `limit` borné `1..MAX_ROWS` par pydantic. `annotations.readOnlyHint=True` : les clients (Claude, Cursor) savent qu'ils peuvent l'appeler sans confirmation. |
| `execute(sql, params)` | `auth=scopes("db:write")` : invisible pour une clé sans ce scope. `destructiveHint=True` : les clients demandent confirmation. N'accepte que `INSERT/UPDATE/DELETE`, une instruction. |

`scopes("db:read")` vient de `forge` : appliqué en HTTP avec clés, transparent en stdio local.

## 8. Ressource et prompt

- `schema://tables` : le schéma complet en une lecture. Certains clients préfèrent charger ce contexte
  d'emblée plutôt que d'enchaîner `describe_table`.
- `analyze(question)` : un prompt réutilisable qui impose la méthode (découvrir → décrire → requêter →
  citer le SQL). Dans Claude Desktop il apparaît comme une commande.

## 9. Ce que `forge` ajoute sans code

Auth par clé, scopes, ligne d'audit JSON par appel (`{"tool":"query","client":"alice","ok":true,"ms":9.7}`),
rate-limit, masquage des exceptions imprévues, `/health` avec readiness, transport stdio/HTTP,
`X-API-Key` accepté, arrêt propre, en-têtes proxy. Voir [`forge/__init__.py`](../forge/__init__.py).

## 10. Tests ([`tests/test_database.py`](../tests/test_database.py))

Garde-fou (5 injections classiques), découverte du schéma, paramètres bindés, plafond + `truncated`,
erreur SQL lisible, écriture puis relecture, `DROP` refusé, ressource, prompt, sérialisation
(`Decimal`, dates, bytes, UUID), options moteur Postgres, `/health`. Base SQLite temporaire, < 1 s.

## 11. Limites connues et évolutions

- Le regex n'analyse pas la grammaire SQL : c'est pourquoi la lecture seule est aussi imposée par la base.
- Pas de limite de coût par requête sur SQLite/MySQL (timeout Postgres seulement) : utiliser un rôle avec
  `max_execution_time` (MySQL) si besoin.
- Pour masquer des colonnes sensibles : exposer des **vues** dans un schéma dédié au rôle du MCP.
- Pour des questions métier récurrentes, préférer une couche sémantique : voir
  [USECASE_ANALYTICS.md](USECASE_ANALYTICS.md).
