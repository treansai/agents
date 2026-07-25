# Déployer l'agent (AI Claims Reviewer) en production

Cible : le serveur prod existant (`traidano-prd`, Scaleway, alias SSH dans
`~/.ssh/config`) qui héberge déjà la stack Agenomic dans `/opt/agenomic`
derrière Caddy. L'agent devient un service de plus dans ce bundle et parle au
gateway Agenomic **en interne** (`http://api-gateway:8080`, même réseau
compose) — pas besoin de sortir par Internet.

**Règle absolue du bundle** : `/opt/agenomic` ne référence que des images du
registre `rg.fr-par.scw.cloud/agenomic-registry` — jamais de section `build:`.
On construit donc l'image ailleurs, on la pousse au registre, et le compose la
tire.

Fichiers prêts à l'emploi dans `agents/demo/deploy/` :
`docker-compose.agents.prod.yml` (overlay durci : non-root, read-only,
cap_drop ALL, volume dédié, 1 worker), `Caddyfile.agents.snippet` (vhost TLS),
`env.agents.example` (bloc d'env à compléter).

---

## Étape 1 — Construire et pousser l'image au registre

Le serveur est en x86_64 : builder en `linux/amd64` (depuis le Mac ARM,
`--platform` s'impose).

```bash
cd /Users/gabinmberikongo/code/treansai/agm/agents

# Login registre Scaleway (secret key SCW dans ton gestionnaire de secrets)
docker login rg.fr-par.scw.cloud/agenomic-registry -u nologin --password-stdin

# Build + push multi-plateforme
docker buildx build --platform linux/amd64 \
  -t rg.fr-par.scw.cloud/agenomic-registry/demo-agents:v1.0.0 \
  --push .
```

Amélioration durable (optionnel) : créer un repo GitHub pour `agents/` et
copier le workflow `build-image.yml` d'`agenomic-web` (déclenché par
`gh release create`, secrets `SCW_REGISTRY_URL`/`SCW_SECRET_KEY`,
environnement `staging`) — même modèle « tag-and-release means deploy ».

## Étape 2 — DNS

Créer l'enregistrement pour `agents.agenomic.io` (même zone que les autres
domaines) pointant vers le serveur prod : `AAAA` →
`2001:bc8:1210:c159:dc00:ff:febb:3c39` (+ `A` si une IPv4 est attachée).
Caddy obtiendra le certificat tout seul au premier `up`.

## Étape 3 — Préparer l'organisation Agenomic côté cloud

Si pas encore fait (voir `ETAPES.md` section B) : obtenir la clé `agm_` de
l'org, puis pousser le bundle de l'agent et noter son UUID :

```bash
agm cloud login --profile prod --endpoint https://api.agenomic.io --api-key <clé>
agm validate agent-bundle --level strict
agm --profile prod cloud push-agent agent-bundle/dist/claims-reviewer-v1.0.0.bundle.tar.zst \
    --name "AI Claims Reviewer" --version v1.0.0
# → noter agent UUID + bundle id ; release auto-créée (awaiting_approval)
```

## Étape 4 — Configurer le serveur

```bash
# 4.1 Copier l'overlay et le bloc Caddy
scp agents/demo/deploy/docker-compose.agents.prod.yml traidano-prd:/opt/agenomic/

# 4.2 Sur le serveur : compléter /opt/agenomic/.env avec le bloc de
#     agents/demo/deploy/env.agents.example (secrets >= 32 caractères :
#     python3 -c "import secrets; print(secrets.token_urlsafe(48))")
ssh traidano-prd
vi /opt/agenomic/.env          # AGENTS_DOMAIN, AGENTS_IMAGE_TAG, secrets,
                               # clé agm_, agent UUID, GOOGLE_API_KEY si LLM

# 4.3 Ajouter le vhost au Caddyfile (bloc de Caddyfile.agents.snippet)
vi /opt/agenomic/Caddyfile
```

Sans `GOOGLE_API_KEY`, les endpoints HTTP `/v1/claims/review` échoueront à
l'appel ADK — la démo déterministe passe alors par le CLI dans le conteneur
(étape 6). Avec la clé, tout marche en HTTP.

## Étape 5 — Démarrer

```bash
# Toujours sur le serveur
cd /opt/agenomic
docker compose -f docker-compose.prod.yml -f docker-compose.agents.prod.yml pull agents
docker compose -f docker-compose.prod.yml -f docker-compose.agents.prod.yml up -d agents
docker compose -f docker-compose.prod.yml -f docker-compose.agents.prod.yml restart caddy  # recharge le Caddyfile

docker compose -f docker-compose.prod.yml -f docker-compose.agents.prod.yml ps agents
docker logs agenomic-agents-1 --tail 20    # attendre "application_started"
```

## Étape 6 — Vérifier

```bash
# Santé (depuis n'importe où)
curl -sS https://agents.agenomic.io/healthz     # {"status":"ok"}
curl -sS https://agents.agenomic.io/readyz      # ledger + ATEP valides

# Un run métier via l'API (nécessite GOOGLE_API_KEY côté serveur)
curl -sS https://agents.agenomic.io/v1/claims/review \
  -H 'Content-Type: application/json' -H "X-API-Key: $AGENTS_APP_API_KEY" \
  -d @examples/claim-high-value.json            # → decision human_review

# Ou, sans clé LLM : run déterministe DANS le conteneur
ssh traidano-prd 'docker exec agenomic-agents-1 \
  python -m agenomic_agents.cli claims /dev/stdin --no-llm' \
  < examples/claim-high-value.json
# (éviter pendant qu'un trafic HTTP tourne : ledger SQLite mono-écrivain)
```

Puis sur **https://app.agenomic.io** : la session de tracking du service
apparaît dans `/tracking` (créée au premier run, epinglable via
`APP_AGENOMIC_TRACKING_SESSION`), les traces alimentent l'agent dans
`/registry`, et `GET /v1/agents/<UUID>/drift?from=&to=` calcule le drift.

## Étape 7 — Mise à jour / rollback

```bash
# Nouvelle version : rebuild + push (étape 1) avec un nouveau tag, puis :
ssh traidano-prd 'sed -i "s/^AGENTS_IMAGE_TAG=.*/AGENTS_IMAGE_TAG=v1.1.0/" /opt/agenomic/.env && \
  cd /opt/agenomic && \
  docker compose -f docker-compose.prod.yml -f docker-compose.agents.prod.yml pull agents && \
  docker compose -f docker-compose.prod.yml -f docker-compose.agents.prod.yml up -d agents'
# Rollback = remettre l'ancien tag et re-up (les images restent au registre).
```

Chaque release applicative doit avoir son pendant côté gouvernance :
`agm build` + `agm --profile prod cloud push-agent --agent-id <UUID>
--version vX.Y.Z` — c'est exactement ce désalignement (code déployé ≠ bundle
locké) que la démo de drift met en scène.

---

## Contraintes & notes d'exploitation

- **1 worker, 1 réplique** : le SDK Agenomic 0.1 a un écrivain ATEP local
  sérialisé par processus ; le volume `agents-data` est à écrivain unique.
  Pour scaler, remplacer l'export local par le backend partagé avant.
- **Secrets** : `APP_API_KEY` et `APP_LEDGER_HMAC_KEY` ≥ 32 caractères sinon
  refus au démarrage (`APP_ENV=production`). Jamais de secrets dans l'image.
- **Logs/observabilité** : les logs conteneur partent automatiquement dans
  Loki via Alloy (stack déjà en place) — visibles dans Grafana
  (`grafana.agenomic.io`).
- **Trace events / drift BDS** : nécessite le fix `trace_events.org_id`
  déployé côté gateway (voir `ETAPES.md` section C). Sans lui, la glue
  retombe automatiquement sur l'upload sans events (aucune casse, BDS à 0).
- **Confidentialité** : aucun contenu de sinistre ne sort du service — les
  traces Agenomic ne portent que hashes BLAKE3, labels et corrélations.
- **Sauvegarde** : inclure le volume `agents-data` (ledger signé + segments
  ATEP + clé Ed25519 `signing-key.pem`) dans la stratégie de backup du
  serveur.
