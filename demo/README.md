# Démo : drift intentionnel du Claims Reviewer, gouverné par Agenomic

Scénario « hotfix silencieux » : un correctif pressé relève la gate de revue
humaine de 50 000 € à 500 000 €. Le même sinistre de 75 000 € passe de
`human_review` à `approve` sans humain. La plateforme app.agenomic.io détecte
le drift, bloque la release et fait valider la correction.

## Pré-requis

- `agents/.env` complété avec le bloc Agenomic Cloud (voir `.env.example`) :
  `APP_AGENOMIC_ENDPOINT` (= `https://api.agenomic.io`), `APP_AGENOMIC_API_KEY`
  (clé `agm_` de l'organisation), `APP_AGENOMIC_AGENT_UUID` (rempli à l'acte 1).
- CLI : `agm` (`agenomic/agenomic-cli/target/release/agm`), profil `prod`
  connecté (`agm cloud login --profile prod --endpoint https://api.agenomic.io
  --api-key <clé>`).
- Python : `source .venv/bin/activate` dans `agents/`.

## Acte 1 — Baseline en production

```bash
cd agents
agm validate agent-bundle --level strict                       # VALID, 0 warning
agm build agent-bundle -o agent-bundle/dist/claims-reviewer-v1.0.0.bundle.tar.zst
agm --profile prod cloud push-agent agent-bundle/dist/claims-reviewer-v1.0.0.bundle.tar.zst \
    --name "AI Claims Reviewer" --version v1.0.0 \
    --description "ADK claims pipeline; deterministic gate keeps the decision"
# → noter agent_id (UUID) et bundle_id. L'upload auto-crée une release ; pour
#   des notes propres, créer la release explicitement :
agm --profile prod cloud push-release --agent-id <AGENT_UUID> --bundle-id <BUNDLE_V1> \
    --version v1.0.0-baseline --notes "Baseline: human review >= 50k EUR"
# Renseigner APP_AGENOMIC_AGENT_UUID=<AGENT_UUID> dans .env
```

Pièges appris en répétition : toujours passer `--agent-id` aux push suivants
(sinon un agent doublon est créé — pas de DELETE) et un `--version` unique par
bundle ; un contenu identique ne peut pas être re-uploadé pour le même agent
(hash unique) — la release corrective épingle le bundle baseline existant.

Runs de référence (gratuits, déterministes) :

```bash
agenomic-agents claims examples/claim.json --no-llm             # 1 250 € → approve
agenomic-agents claims examples/claim-high-value.json --no-llm  # 75 000 € → human_review
```

Chaque run pousse sa trace (`POST /v1/traces`) et ses événements de tracking ;
la première exécution ouvre la session (`agenomic_tracking_session_started`
dans les logs — noter le `session_id`). Vérifier : `demo/tracking-status.sh
<SESSION> alerts` → `[]` (aucune alerte, comportement conforme).

## Acte 2 — Le drift

```bash
demo/drift-apply.sh
agenomic-agents claims examples/claim-high-value.json --no-llm  # 75 000 € → approve (!)
```

Détection immédiate côté plateforme (le log du run affiche l'alerte reçue) :
`intent.detected = auto_approve:high_value_claim` → alerte **Intent /
critical**, `blocks_release=true`, `requires_human_review=true`.

Relancer 3 fois le même payload → alerte **Loop / warning** (même outil, même
input_hash) : parfait pour montrer le flux en direct.

La release driftée « propre en apparence » :

```bash
agm build agent-bundle -o agent-bundle/dist/claims-reviewer-v1.1.0.bundle.tar.zst
agm diff agent-bundle/dist/claims-reviewer-v1.0.0.bundle.tar.zst \
         agent-bundle/dist/claims-reviewer-v1.1.0.bundle.tar.zst   # → prompt_changed (medium)
agm --profile prod cloud push-agent agent-bundle/dist/claims-reviewer-v1.1.0.bundle.tar.zst \
    --name "AI Claims Reviewer" --agent-id <AGENT_UUID> --version v1.1.0
# (l'upload crée la release v1.1.0 automatiquement)
```

Drift statistique sur la fenêtre de traces (baseline vs courant) — la
plateforme coupe la fenêtre en son milieu, donc caler le point médian entre la
phase baseline et la phase driftée (forme à 2 arguments pour une coupe nette) :

```bash
demo/compute-drift.sh 10                                       # ou :
demo/compute-drift.sh 2026-07-26T09:00:00Z 2026-07-26T09:06:00Z
# attendu après drift : bds > seuil, alert=true,
# reason="behavior changed without genome_version change" (drift NON intentionnel)
```

## Acte 3 — État de l'agent & validation des corrections

Console **https://app.agenomic.io** : `/registry` (l'agent), `/releases`
(v1.1.0 candidate + diff), `/proposal` (file de revue Article 14),
`/tracking` (session et alertes).

Correction :

```bash
demo/drift-revert.sh
make test                                                        # la gate CI repasse
# Le correctif restaure exactement le bundle baseline (même hash) : la release
# corrective épingle donc le bundle v1.0.0 d'origine — preuve de restauration.
agm --profile prod cloud push-release --agent-id <AGENT_UUID> --bundle-id <BUNDLE_V1> \
    --version v1.0.1 --notes "Fix: restore the 50k human-review gate (pins the baseline bundle)"
```

Sur la console : approuver v1.0.1 (approve → promote → `production`), laisser
v1.1.0 en `awaiting_approval` ou la rejeter. Équivalent API :
`POST /v1/releases/:id/approve` (body `{"comment": "..."}`), `/promote`,
`/rollback`.

## Acte 4 — Live tracking

```bash
demo/watch-tracking.sh <SESSION>          # flux SSE dans un terminal
# dans un autre terminal : série de runs mixtes
agenomic-agents claims examples/claim.json --no-llm
agenomic-agents claims examples/claim-high-value.json --no-llm
demo/tracking-status.sh <SESSION> report  # rapport final (pass/warn/fail)
```

Console : `/tracking/<SESSION>` (rendu serveur — rafraîchir la page).

## Remise à zéro

`demo/drift-revert.sh` restaure code et bundle ; les sessions de tracking se
ferment via `POST /v1/tracking/sessions/:id/stop`. Les releases restent en
historique — c'est le but (chaîne d'audit).
