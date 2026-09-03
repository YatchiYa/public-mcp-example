# Déployer le MCP sur une instance avec un sous-domaine HTTPS

Objectif : `https://mcp.votredomaine.com/mcp` consommable par n'importe quel agent, en ~30 minutes.

## Prérequis

- Une instance Linux (VPS OVH/Hetzner/Scaleway, VM GCP/AWS/Azure…), 1 vCPU / 1 Go suffisent.
- Ports **80** et **443** ouverts (groupe de sécurité / firewall).
- Un sous-domaine : enregistrement DNS **A** `mcp.votredomaine.com → IP de l'instance`.
- Accès réseau de l'instance vers la base de données (IP autorisée côté DB).

## 1. Rôle base de données en lecture seule

Postgres :

```sql
CREATE ROLE mcp_readonly LOGIN PASSWORD 'un-mot-de-passe-fort';
GRANT CONNECT ON DATABASE prod TO mcp_readonly;
GRANT USAGE ON SCHEMA public TO mcp_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO mcp_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO mcp_readonly;
ALTER ROLE mcp_readonly SET statement_timeout = '15s';   -- protège la base des requêtes lourdes
```

MySQL : `CREATE USER 'mcp_readonly'@'%' IDENTIFIED BY '…'; GRANT SELECT ON prod.* TO 'mcp_readonly'@'%';`

Pour n'exposer qu'un sous-ensemble : créer un schéma `mcp` avec des **vues**, et ne donner `SELECT`
que sur ce schéma.

## 2. Installer Docker sur l'instance

```bash
ssh ubuntu@IP
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER && newgrp docker
```

## 3. Récupérer le code et configurer

```bash
git clone <votre-repo> mcp && cd mcp
cp deploy/.env.example deploy/.env
openssl rand -hex 32     # une clé par consommateur, à coller dans MCP_API_KEYS
nano deploy/.env         # DATABASE_URL + MCP_API_KEYS
nano deploy/Caddyfile    # remplacer mcp.votredomaine.com
```

`deploy/.env` final ressemble à :

```
MCP_API_KEYS=3f9c…a1:agent-analytics:db:read;7b2e…c4:agent-admin:db:read|db:write
DATABASE_URL=postgresql+psycopg://mcp_readonly:motdepasse@10.0.0.5:5432/prod
DB_MAX_ROWS=200
MCP_RATE_LIMIT_RPS=20
```

## 4. Lancer

```bash
docker compose -f deploy/docker-compose.prod.yml up -d --build
docker compose -f deploy/docker-compose.prod.yml logs -f      # Ctrl+C pour quitter
```

Caddy obtient le certificat Let's Encrypt automatiquement (le DNS doit déjà pointer sur l'instance).
Le conteneur MCP n'est **pas** exposé directement : seul Caddy (443) y accède.

## 5. Vérifier

```bash
curl https://mcp.votredomaine.com/health
# {"status":"ok","server":"database","auth":true}

curl -s -o /dev/null -w "%{http_code}\n" -X POST https://mcp.votredomaine.com/mcp -d '{}'
# 401  (pas de clé = refusé)

npx -y @modelcontextprotocol/inspector --cli https://mcp.votredomaine.com/mcp --transport http \
  --header "Authorization: Bearer <clé>" --method tools/list
# liste list_tables / describe_table / query (+ execute si la clé a db:write)
```

Depuis Google ADK :

```python
McpToolset(connection_params=StreamableHTTPConnectionParams(
    url="https://mcp.votredomaine.com/mcp", headers={"Authorization": "Bearer <clé>"}))
```

## 6. Exploitation

| Action | Commande |
|---|---|
| Logs / audit (qui appelle quoi, durée, erreurs) | `docker compose -f deploy/docker-compose.prod.yml logs -f database` |
| Ajouter / révoquer une clé | éditer `MCP_API_KEYS` dans `deploy/.env` puis `docker compose -f deploy/docker-compose.prod.yml up -d` |
| Mettre à jour le code | `git pull && docker compose -f deploy/docker-compose.prod.yml up -d --build` |
| Arrêter | `docker compose -f deploy/docker-compose.prod.yml down` |
| Santé (monitoring externe) | `GET https://mcp.votredomaine.com/health` → 200 |

## 7. Variantes

- **Plusieurs MCP** (ex. `database` + `travel`) : dupliquer le service dans le compose avec
  `SERVER: servers.travel`, et dans le Caddyfile `handle_path /travel/* { reverse_proxy travel:8000 }`
  ou un second sous-domaine.
- **Cloud managé sans VM** : l'image se déploie telle quelle sur Cloud Run / Fly.io / Render / ECS
  (port 8000, variables d'env identiques, TLS fourni par la plateforme, plus besoin de Caddy).
- **Test rapide avant DNS** : `cloudflared tunnel --url http://localhost:8001` donne une URL https
  temporaire consommable par les clients cloud (API Claude, Gemini).
- **Passer d'une clé API à OAuth/JWT** (SSO d'entreprise) : remplacer `ApiKeyVerifier` par
  `JWTVerifier(jwks_uri=…, issuer=…, audience=…)` dans `forge/__init__.py`. Rien à changer côté serveurs.
