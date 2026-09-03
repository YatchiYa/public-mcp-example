# Cas d'usage : le MCP `travel-intel`, données externes temps réel sans clé

Fichier : [`servers/travel/server.py`](../servers/travel/server.py) + [`api.py`](../servers/travel/api.py).
Serveur : `python -m forge run servers.travel` (port 8002 via `./up.sh`).

## 1. Le problème que résout ce serveur

Un LLM ne connaît ni la météo de demain, ni le taux EUR/JPY du jour, ni l'heure qu'il est à Tokyo. Ce
serveur montre le **deuxième grand pattern MCP** (après « exposer ma base ») : **exposer des APIs
externes** à l'agent, de façon fiable, en lui donnant des outils **composables** et des erreurs qu'il
sait corriger.

Il tourne partout sans aucune clé tierce (Open-Meteo, Frankfurter/BCE, `zoneinfo` de Python), ce qui en
fait aussi le serveur de **démo et de test** du kit.

## 2. Ce que l'agent voit

| Outil | Source | Rôle |
|---|---|---|
| `geocode_place(query, limit)` | Open-Meteo Geocoding | nom → lat/lon, pays, **timezone**, population. **À appeler en premier.** |
| `get_weather(latitude, longitude, days)` | Open-Meteo Forecast | conditions actuelles + prévisions quotidiennes (°C, km/h, % pluie), codes WMO traduits en texte |
| `get_air_quality(latitude, longitude)` | Open-Meteo Air Quality | AQI européen + PM2.5/PM10, avec une note lisible (good … extremely poor) |
| `convert_currency(amount, from, to)` | Frankfurter (taux BCE) | conversion du jour, taux et date renvoyés |
| `local_time(timezone)` | `zoneinfo` (stdlib) | heure locale et décalage UTC, sans réseau |

Prompt `plan_trip(destination, days)` : impose la chaîne géocoder → météo → air → heure → budget.

Instructions envoyées au LLM : « flux typique : `geocode_place` puis `get_weather`/`get_air_quality` avec
les coordonnées renvoyées ; `local_time` avec la timezone renvoyée par `geocode_place` ».

## 3. Les mécanismes à retenir

### 3.1 Outils composables plutôt qu'un « super outil »

`get_weather` prend des coordonnées, pas un nom de ville. Un outil = une source = une responsabilité ;
l'agent enchaîne. Les **instructions** et les **descriptions** disent l'ordre. Résultat : moins d'ambiguïté
(« Paris, Texas ? »), et chaque outil reste testable seul.

### 3.2 Validation avant réseau

```python
latitude: Annotated[float, Field(ge=-90, le=90)]
days: Annotated[int, Field(ge=1, le=16)]
from_currency: Annotated[str, Field(min_length=3, max_length=3)]
```

Pydantic refuse `latitude=999` avant tout appel HTTP ; l'API tierce ne voit jamais de requête invalide.

### 3.3 Erreurs traduites pour le LLM

```python
except httpx.HTTPStatusError as e: raise ToolError(f"Upstream API error {code} from {host}")
except httpx.HTTPError as e:       raise ToolError(f"Network error reaching {host}: {type(e).__name__}")
```

Plus : `No place found for 'Xyz'. Try a larger city or add the country.`, `Unknown timezone…; use the
value returned by geocode_place`. Chaque message dit **quoi faire**, l'agent se corrige au tour suivant.

### 3.4 Sorties normalisées

L'API renvoie `weather_code: 61` et des tableaux parallèles ; l'outil renvoie
`{"date": "2026-09-03", "min_c": 18.3, "max_c": 26.1, "rain_probability_pct": 10, "conditions": "light rain"}`.
On fait le travail de mise en forme **dans le serveur**, pas dans le prompt : moins de tokens, moins d'erreurs.

### 3.5 Client HTTP partagé, timeouts, User-Agent

`api.py` : un `httpx.AsyncClient` partagé (keep-alive), timeout 10 s, `User-Agent` identifié (les APIs
publiques le demandent). Les tools sont `async` : un appel lent ne bloque pas les autres.

### 3.6 Tests sans réseau

`tests/test_travel.py` remplace le transport httpx par `httpx.MockTransport` : flux complet, erreurs 404,
lieu introuvable, timezone inconnue, validation, en 0,1 s et sans dépendre d'Open-Meteo.

## 4. Démo pas à pas

```bash
./up.sh                                    # travel -> http://<IP>:8002/mcp   (clé : dev-key-alice ou dev-key-bob)

npx -y @modelcontextprotocol/inspector --cli http://localhost:8002/mcp --transport http \
  --header "Authorization: Bearer dev-key-bob" --method tools/call --tool-name geocode_place --tool-arg query=Kyoto --tool-arg limit=1
npx -y @modelcontextprotocol/inspector --cli http://localhost:8002/mcp --transport http \
  --header "Authorization: Bearer dev-key-bob" --method tools/call --tool-name get_weather --tool-arg latitude=35.02 --tool-arg longitude=135.75 --tool-arg days=3
npx -y @modelcontextprotocol/inspector --cli http://localhost:8002/mcp --transport http \
  --header "Authorization: Bearer dev-key-bob" --method tools/call --tool-name convert_currency --tool-arg amount=200 --tool-arg from_currency=EUR --tool-arg to_currency=JPY
```

Avec Google ADK (agent complet dans [`examples/adk_agent/`](../examples/adk_agent/)) :

```python
McpToolset(connection_params=StreamableHTTPConnectionParams(
    url="http://<IP>:8002/mcp", headers={"Authorization": "Bearer dev-key-bob"}))
```

Questions à poser à l'agent :
- « Il fait quel temps à Kyoto ce week-end, et 200 € font combien en yens ? » → 3 outils enchaînés
- « Quelle heure est-il à Lisbonne, et l'air y est-il respirable ? » → `geocode_place`, `local_time`, `get_air_quality`
- « Prépare-moi un brief de 3 jours pour Marrakech » → prompt `plan_trip`

En stdio (Claude Desktop, sans clé) : voir [`examples/clients/claude_desktop_config.json`](../examples/clients/claude_desktop_config.json).

## 5. Adapter : exposer *votre* API externe ou interne

1. `python -m forge new mon_api` puis copiez le pattern de `api.py` : un client httpx partagé, une
   fonction par endpoint, la clé tierce en variable d'environnement (`headers={"Authorization": …}`).
2. Un outil par action métier, coordonnées/identifiants en entrée, **jamais** de texte libre que l'API ne
   valide pas. Validez avec `Field(...)`.
3. Enveloppez chaque appel dans `_call()` : les pannes réseau deviennent des `ToolError` actionnables.
4. Normalisez la sortie (noms clairs, unités dans les noms : `temperature_c`, `wind_kmh`).
5. Marquez `readOnlyHint` / `openWorldHint` ; les clients s'en servent pour décider s'il faut confirmer.
6. Testez avec `httpx.MockTransport` : une réponse enregistrée par endpoint suffit.

## 6. Ce que ça donne en production

- Stateless, sans secret pour ce serveur précis ; en cas d'API payante, la clé reste **côté serveur**, les
  agents n'ont que leur clé MCP (révocable, auditée, rate-limitée).
- Une panne de l'API tierce ne casse pas l'agent : il reçoit un message et peut répondre « la météo est
  indisponible, voici le reste ».
- Le même serveur alimente tous les canaux (ADK, Claude, ChatGPT, Cursor) avec une seule URL.
