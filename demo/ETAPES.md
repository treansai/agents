# Tes étapes — démo drift Agenomic (Claims Reviewer)

Tout est construit et **validé en local de bout en bout**. Ce document liste ce
qu'il te reste à faire : (A) rejouer la démo locale en 10 minutes, (B) passer
en production app.agenomic.io quand tu le décides, (C) livrer le correctif
plateforme découvert pendant la préparation.

État au 2026-07-26 : stack cloud local UP (gateway rebuildé avec le fix
`trace_events.org_id`), console locale UP sur :3001, agent
`AI Claims Reviewer` poussé (UUID `9eab9ae6-dcdd-4b14-b519-b923b26be7ef`),
releases v1.0.0 / v1.1.0 (driftée) / v1.0.1 (**production**), session de
tracking active `01KYDR0FV0X8A58TA4RKKB3EVY` avec 2 runs baseline propres.

---

## A. Rejouer la démo locale (tout est prêt)

Pré-requis déjà en place : `agents/.env` configuré (endpoint local + clé e2e +
UUID agent), venv réinstallé, bundle validé (`agm validate` : 0 erreur).

### A.1 Ouvrir la console

1. `http://localhost:3001/login` — identifiants dans
   `agents/demo/.local/console-credentials.txt` (compte local
   `demo@traidano.com`, rattaché à l'org e2e en `maintainer`).
2. Pages utiles : `/registry` (l'agent), `/releases` (v1.0.1 production,
   v1.1.0 en attente), `/tracking` (sessions), `/proposal` (revue Art. 14).

Si la console ne répond plus : `cd agenomic_private/agenomic-web/apps/app &&
pnpm dev` (port 3001). Si le gateway ne répond plus :
`cd agenomic_private/agenomic-cloud/infra/local && docker compose up -d`.

### A.2 Dérouler le scénario (terminal dans `agents/`, venv activé)

```bash
# Acte 1 — baseline (session déjà ouverte, sinon: demo/start-session.sh)
agenomic-agents claims examples/claim.json --no-llm             # → approve
agenomic-agents claims examples/claim-high-value.json --no-llm  # → human_review
demo/tracking-status.sh $APP_AGENOMIC_TRACKING_SESSION alerts   # → []

# Acte 2 — le drift (terminal 2 : demo/watch-tracking.sh <SESSION> pour le live)
demo/drift-apply.sh
agenomic-agents claims examples/claim-high-value.json --no-llm  # → approve (!)
#   ↳ alerte CRITICAL "auto_approve:high_value_claim" visible dans les logs,
#     le flux SSE, et /tracking/<SESSION> sur la console (rafraîchir)
agenomic-agents claims examples/claim-high-value.json --no-llm  # ×3 → alerte Loop
make test                                                       # gate CI rouge

# Acte 3 — drift statistique (fenêtre coupée entre baseline et drift)
demo/compute-drift.sh 10        # bds > seuil, alert=true, "behavior changed
                                #  without genome_version change"

# Acte 4 — correction + validation humaine
demo/drift-revert.sh && make test                               # 20/20 verts
# Sur la console /releases : v1.0.1 est déjà approuvée+promue (démo API) ;
# pour le geste live, utilise la v1.1.0 restée en awaiting_approval → reject.

# Clôture
demo/stop-session.sh            # rapport final: fail (1 critical, 2 warnings)
```

Le déroulé complet commenté est dans `agents/demo/README.md`.

---

## B. Passage en production app.agenomic.io (tes actions)

Je suis bloqué par le classifieur de permissions pour créer des credentials en
prod — c'est à toi de faire l'étape B.1, le reste est scriptable par moi.

### B.1 Obtenir une clé API `agm_` (une seule fois)

Option A — bootstrap d'une org de démo (tape-le dans la session Claude, le `!`
l'exécute) :

```
! curl -sS -X POST https://api.agenomic.io/v1/orgs/bootstrap -H 'Content-Type: application/json' -d '{"name":"Traidano Demo","owner_email":"gabin.mberikongo@traidano.com","owner_display_name":"Gabin Mberikongo"}' -o /Users/gabinmberikongo/code/treansai/agm/agents/.demo-bootstrap.json && echo OK
```

Option B — depuis ton compte console : app.agenomic.io → `/me/keys` → créer
une clé, puis la coller dans `agents/.env` (`APP_AGENOMIC_API_KEY=...`).

### B.2 Connecter le CLI et l'agent à la prod

```bash
agm cloud login --profile prod --endpoint https://api.agenomic.io --api-key <la_clé>
agm --profile prod cloud whoami          # vérifie l'accès
```

Dans `agents/.env` : `APP_AGENOMIC_ENDPOINT=https://api.agenomic.io`,
`APP_AGENOMIC_API_KEY=<la_clé>`, et vider `APP_AGENOMIC_TRACKING_SESSION`.
⚠️ L'auth API est le header `x-api-key` (géré partout dans les scripts).

### B.3 Pousser l'agent et rejouer les actes

```bash
agm validate agent-bundle --level strict
agm --profile prod cloud push-agent agent-bundle/dist/claims-reviewer-v1.0.0.bundle.tar.zst \
    --name "AI Claims Reviewer" --version v1.0.0 \
    --description "ADK claims pipeline; deterministic gate keeps the decision"
# → noter l'agent UUID → APP_AGENOMIC_AGENT_UUID dans .env
# puis dérouler les Actes 1-4 du README (identiques au local)
```

Rappels appris en répétition : `--agent-id` obligatoire dès le 2ᵉ push (sinon
doublon sans DELETE possible), `--version` unique par bundle, contenu
identique = 409 → la release corrective épingle le bundle baseline via
`push-release --bundle-id <bundle_v1.0.0>`.

### B.4 Console prod

`https://app.agenomic.io` (compte avec email vérifié requis) : `/registry`,
`/releases` (approve → promote la v1.0.1, reject la v1.1.0), `/proposal`,
`/tracking/<SESSION>`. Le flux SSE brut :
`demo/watch-tracking.sh <SESSION>`.

---

## C. Livrer le correctif plateforme (recommandé avant la démo prod)

Bug corrigé en local pendant la préparation : `POST /v1/traces` avec `events`
renvoyait 500 (`INSERT trace_events` sans `org_id`, NOT NULL). Fichier :
`agenomic_private/agenomic-cloud/crates/agenomic-db/src/postgres.rs` (INSERT
complété avec `org_id`). Les images du registre (prod incluse) ont encore le
bug — sans ce fix, les trace events (et donc le drift BDS) ne marchent pas en
prod. La glue a un fallback (retry sans events), donc rien ne casse, mais le
BDS restera à 0.

1. Commit + push du fix dans `agenomic-cloud` (branche + PR selon ton flux).
2. `gh release create vX.Y.Z` (ou `gh workflow run build-image.yml -f tag=…`)
   → build + push registre Scaleway.
3. Sur le serveur (`ssh traidano-prd`) : bump `CLOUD_IMAGE_TAG` dans
   `/opt/agenomic/.env` puis `docker compose -f docker-compose.prod.yml pull
   && up -d`. (Règle : images registre uniquement, jamais de build local.)

---

## Notes

- Le drift est réversible à tout moment : `demo/drift-revert.sh` (sauvegardes
  dans `demo/.baseline/`).
- Les traces Agenomic ne contiennent jamais le contenu métier : hashes BLAKE3,
  labels et corrélations uniquement (`capture_input/output=False` conservés).
- Répétition locale complète archivée : alerte Intent CRITICAL
  (`blocks_release=true`), alerte Loop, diff de bundle `prompt_changed`,
  BDS 0,156 `alert=true` (« behavior changed without genome_version change »),
  release v1.0.1 approuvée → promue `production`, rapport de session `fail`.
